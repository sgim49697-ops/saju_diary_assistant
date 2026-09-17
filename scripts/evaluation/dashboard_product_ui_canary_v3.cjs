// dashboard_product_ui_canary_v3.cjs - v1.20 실제 자산을 외부 접속·모델 호출 없이 합성 HTTP로 검증한다.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.SAJU_PLAYWRIGHT_MODULE);
const assets = path.resolve(__dirname, "../training/phase5_dashboard_assets/v1.20.0");
let stage = "시작";

async function main() {
  const browser = await chromium.launch({ executablePath: process.env.SAJU_CHROMIUM_EXECUTABLE, headless: true, args: ["--disable-gpu", "--no-sandbox"] });
  let passed = 0;
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.addInitScript(() => { window.setInterval = () => 0; });
      let kind = "direct_fact", failCalculation = true;
      let generatedRequests = 0, deletions = 0, latestPayload = null;
      let sessionNumber = 9, runtimeNumber = 12, runtimeEvents = 0;
      const savedSessions = new Map();
      const delayed = [];
      const defer = (method, pathname, fail = false) => {
        let release, captured;
        const gate = { method, pathname, fail, claimed: false, wait: new Promise((resolve) => { release = resolve; }), seen: new Promise((resolve) => { captured = resolve; }) };
        gate.capture = captured;
        gate.release = async () => {
          const response = page.waitForResponse((r) => new URL(r.url()).pathname === pathname && r.request().method() === method);
          release();
          await (await response).finished();
          await page.evaluate(() => new Promise(requestAnimationFrame));
        };
        delayed.push(gate);
        return gate;
      };
      const snapshots = { lora_r16: { label: "R16 생성 답변", revision: "synthetic-r16", model_sha256: "a".repeat(64) } };
      const profile = { default_profile: "product_v1", bound_profile: "product_v1", items: [{ profile_id: "product_v1", label: "제품 후보", description: "합성 화면 검증" }] };
      const engines = { default_selection: "lora_r16", selections: [{ selection_id: "lora_r16", label: "R16", mode: "single", engine_ids: ["lora_r16"] }], items: [{ engine_id: "lora_r16", available: true, ...snapshots.lora_r16 }] };
      let session = null;
      const resultFor = (payload, responseKind) => {
        const text = responseKind === "model_generated" ? "합성 모델 원출력 <img src=x onerror=alert(1)>" : responseKind === "direct_fact" ? "연결된 원국의 일간은 병화(丙)입니다." : responseKind === "clarification" ? "운세인지 일상인지 확인해 주세요." : "요청한 범위는 지원하지 않습니다.";
        const mode = { model_generated: "general", direct_fact: "fact_lookup", clarification: "confirmation", blocked: "unsupported" }[responseKind];
        const diagnostics = { response_kind: responseKind, response_mode: mode, notices: [], input_tokens: 50, elapsed_seconds: 0.1, peak_allocated_bytes: 0, grounding_warnings: [] };
        const sessionId = payload.session_id || (++sessionNumber).toString(16).padStart(24, "0");
        const previous = savedSessions.get(sessionId);
        session = {
          session_id: sessionId, turn_count: (previous?.turn_count || 0) + 1,
          updated_at_utc: "2026-09-16T00:00:00Z", prompt_profile: "product_v1", engine_selection: "lora_r16", engine_snapshots: snapshots,
          runtime_binding_sha256: "b".repeat(64), runtime_snapshot_sha256: "c".repeat(64),
          messages: [...(previous?.messages || []), { role: "user", content: payload.prompt, created_at_utc: "2026-09-16T00:00:00Z" }, { role: "assistant", engine_id: responseKind === "model_generated" ? "lora_r16" : null, response_kind: responseKind, response_mode: mode, content: text, diagnostics, created_at_utc: "2026-09-16T00:00:00Z" }],
        };
        savedSessions.set(sessionId, session);
        return { session_id: session.session_id, session, output: text, response_kind: responseKind, runtime_binding_applied: Boolean(payload.runtime_session_id), contexts: {}, persisted: true, model_calls: 0 };
      };
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        assert.equal(url.hostname, "saju-canary.invalid");
        const responseKind = kind;
        const payload = route.request().method() === "POST" ? route.request().postDataJSON() : null;
        if (url.pathname === "/api/generate") { generatedRequests += 1; latestPayload = payload; }
        if (url.pathname.endsWith("/events")) runtimeEvents += 1;
        const gate = delayed.find((g) => !g.claimed && g.method === route.request().method() && g.pathname === url.pathname);
        if (gate) {
          gate.claimed = true;
          gate.capture(payload);
          await gate.wait;
          if (gate.fail) return route.fulfill({ status: 503, json: { error: "합성 지연 통신 오류", code: "TEST_DELAYED_ERROR" } });
        }
        if (route.request().method() === "DELETE") {
          deletions += 1;
          return route.fulfill({ status: 200, json: { status: "deleted" } });
        }
        if (url.pathname === "/api/generate") {
          return route.fulfill({ status: 200, json: resultFor(payload, responseKind) });
        }
        if (url.pathname === "/api/sessions") return route.fulfill({ status: 200, json: { items: [...savedSessions.values()].map((s) => ({ ...s, title: "합성 후보 대화", runtime_bound: true })), prompt_profiles: profile, inference_engines: engines } });
        if (url.pathname.startsWith("/api/sessions/") && savedSessions.has(url.pathname.split("/").at(-1))) return route.fulfill({ status: 200, json: savedSessions.get(url.pathname.split("/").at(-1)) });
        if (url.pathname === "/api/runtime/sessions") return route.fulfill({ status: 201, json: { session_id: (++runtimeNumber).toString(16).padStart(24, "0"), state_revision: 0 } });
        if (url.pathname.endsWith("/events")) {
          if (failCalculation) return route.fulfill({ status: 409, json: { error: "합성 재계산 실패", code: "RUNTIME_TEST_FAILURE" } });
          return route.fulfill({ status: 200, json: { status: "ready", state_revision: 8, result: { chart: { status: "ok", fact_authority: "HARD_GT" }, period: { status: "ok", fact_authority: "HARD_GT" } } } });
        }
        const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
        if (["index.html", "dashboard.js", "dashboard.css", "prompt-examples.json"].includes(name)) return route.fulfill({ status: 200, contentType: name.endsWith(".js") ? "application/javascript" : name.endsWith(".css") ? "text/css" : name.endsWith(".json") ? "application/json" : "text/html", body: fs.readFileSync(path.join(assets, name), "utf8").replace("__CSRF_TOKEN__", "synthetic-csrf") });
        return route.fulfill({ status: 503, json: { error: "합성 화면 검사: 기타 API 미사용" } });
      });
      await page.goto("http://saju-canary.invalid/");
      await page.locator('[data-tab="model"]').click();
      await page.evaluate(({ profile, engines }) => {
        promptProfileCatalog = profile;
        inferenceEngineCatalog = engines;
        renderModelChecks({ generation_gate: { allowed: false, reasons: ["gpu_not_idle"] }, status: "not_ready" });
        renderEngineSelection("lora_r16", false);
        renderPromptProfile("product_v1", false);
      }, { profile, engines });
      assert.equal(await page.locator("#manual-panel").isVisible(), true);
      assert.equal(await page.locator("#run-probe-button").isVisible(), false);
      assert.equal(await page.locator("#engine-selection-select").isDisabled(), true);
      passed += 1;

      await page.evaluate(() => {
        document.getElementById("runtime-panel").classList.remove("hidden");
        activeRuntimeSessionId = "c".repeat(24);
        renderRuntimeFacts({ state_revision: 7, result: { chart: { status: "partial", fact_authority: "POLICY_BOUND_RULE", limitations: ["시간 미상"] } } });
      });
      assert.equal(await page.locator("#runtime-connect-button").isDisabled(), true);
      assert.equal(await page.locator("#runtime-chart-connect-button").isEnabled(), true);
      await page.locator("#runtime-chart-connect-button").click();
      assert.equal(await page.evaluate(() => connectedRuntimeScope), "chart");
      assert.equal(await page.evaluate(() => activeSessionId), null);
      passed += 1;

      for (const [responseKind, expectedLabel, question] of [
        ["direct_fact", "계산 결과 직접 확인", "내 일간은 뭐야?"],
        ["model_generated", "R16 생성 답변", "사주 말고 내 하소연을 들어줘"],
        ["clarification", "확인 안내", "그럼 내일은 어때?"],
        ["blocked", "지원 범위 안내", "이번 주 운세 봐줘"],
      ]) {
        stage = `${viewport.width} ${responseKind}`;
        kind = responseKind;
        const before = generatedRequests;
        await page.locator("#manual-prompt").fill(question);
        await page.locator("#generate-button").click();
        await page.waitForFunction((label) => [...document.querySelectorAll(".message.assistant strong")].at(-1)?.textContent === label, expectedLabel);
        assert.equal(generatedRequests, before + 1);
        assert.equal(latestPayload.engine_selection, "lora_r16");
        assert.equal(latestPayload.runtime_binding_scope, "chart");
        assert.equal(latestPayload.profile, "product_v1");
        assert.equal(await page.locator(".message.assistant").last().locator("p").textContent(), session.messages.at(-1).content);
        assert.equal(await page.locator(".message.assistant img").count(), 0);
        if (kind !== "model_generated") assert.equal(await page.locator(".message.assistant").last().getByText("모델 호출 없음", { exact: false }).count(), 1);
        assert.equal(await page.evaluate(() => connectedRuntimeSessionId), "c".repeat(24));
        passed += 1;
      }

      await page.evaluate(() => {
        runtimeCanaryStatus = { enabled: true };
        document.getElementById("runtime-chart-form").noValidate = true;
        setRuntimeFormEnabled(true);
        document.getElementById("runtime-consent").checked = true;
        document.getElementById("runtime-calendar").value = "solar";
        document.getElementById("runtime-birth-date").value = "1994-04-18";
        document.getElementById("runtime-time-precision").value = "unknown";
        document.getElementById("runtime-city").value = "대전";
      });
      stage = `${viewport.width} 재계산 실패 보존`;
      await page.locator("#runtime-chart-button").click();
      await page.waitForFunction(() => document.getElementById("runtime-status-copy").textContent.includes("합성 재계산 실패"));
      assert.equal(deletions, 0);
      assert.equal(await page.evaluate(() => activeRuntimeSessionId), "c".repeat(24));
      passed += 1;

      failCalculation = false;
      await page.evaluate(() => {
        // 다음 독립 장면: 정상 원국의 공개 계산 결과를 화면에 다시 전달한다.
        renderRuntimeFacts({ state_revision: 7, result: { chart: { status: "ok", fact_authority: "HARD_GT" } } });
        document.getElementById("runtime-target-date").value = "2026-09-05";
        updateRuntimeConversationBinding();
      });
      stage = `${viewport.width} 새 날짜 연결`;
      await page.locator("#runtime-connect-button").click();
      await page.waitForFunction(() => connectedRuntimeScope === "chart_day");
      assert.equal(await page.evaluate(() => activeSessionId), null);
      assert.equal(await page.evaluate(() => connectedRuntimeDate), "2026-09-05");
      assert.equal(deletions, 0);
      passed += 1;

      const view = () => page.evaluate(() => ({ session: activeSessionId, runtime: activeRuntimeSessionId, binding: connectedRuntimeSessionId, scope: connectedRuntimeScope, date: connectedRuntimeDate, connected: runtimeConversationConnected() }));
      const submit = async (prompt) => { await page.locator("#manual-prompt").fill(prompt); await page.locator("#generate-button").click(); };
      const newBound = async () => { await page.locator("#new-session-button").click(); await page.locator("#runtime-chart-connect-button").click(); };

      stage = `${viewport.width} 이전 성공 응답과 새 원국`;
      kind = "direct_fact";
      const lateSuccess = defer("POST", "/api/generate");
      await submit("이전 연결 사실 조회");
      const oldRequest = await lateSuccess.seen;
      await page.locator("#new-session-button").click();
      await page.locator("#runtime-chart-button").click();
      await page.waitForFunction(() => runtimeChartReady && runtimeOperation === null);
      await page.locator("#runtime-chart-connect-button").click();
      const nextView = await view(), beforeCalls = generatedRequests;
      assert.notEqual(nextView.binding, oldRequest.runtime_session_id);
      await lateSuccess.release();
      assert.deepEqual(await view(), nextView);
      assert.equal(generatedRequests, beforeCalls);
      assert.equal(session.messages.at(-2).content, oldRequest.prompt); // 이전 응답은 서버 측 보존
      passed += 1;

      stage = `${viewport.width} 이전 오류와 새 요청 버튼 소유권`;
      const oldFailure = defer("POST", "/api/generate", true);
      await submit("이전 오류 요청");
      await oldFailure.seen;
      await newBound();
      const newer = defer("POST", "/api/generate");
      await submit("새 요청 보존");
      await newer.seen;
      const pendingView = await view();
      await oldFailure.release();
      assert.deepEqual(await view(), pendingView);
      assert.equal(await page.locator("#generate-button").isDisabled(), true);
      assert.equal(await page.locator("#manual-prompt").inputValue(), "새 요청 보존");
      assert.equal((await page.locator("#session-meta").textContent()).includes("통신 오류"), false);
      await newer.release();
      await page.waitForFunction(() => pendingGeneration === null);
      passed += 1;

      stage = `${viewport.width} 중복 submit 차단`;
      const duplicate = defer("POST", "/api/generate");
      await submit("한 번만 처리");
      await duplicate.seen;
      const once = generatedRequests;
      await page.evaluate(() => document.getElementById("manual-form").dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })));
      await duplicate.release();
      assert.equal(generatedRequests, once);
      passed += 1;

      stage = `${viewport.width} 대화 상세 조회 역전`;
      const [firstId, secondId] = [...savedSessions.keys()];
      await page.evaluate(async () => renderSessions(await api("/api/sessions")));
      const detail = defer("GET", `/api/sessions/${firstId}`);
      await page.locator("#session-select").selectOption(firstId);
      await detail.seen;
      await page.locator("#session-select").selectOption(secondId);
      await page.waitForFunction((id) => activeSessionId === id && loadedSessionUpdatedAt !== null, secondId);
      const detailView = await view();
      const visibleBefore = await page.locator(".message.user").last().textContent();
      await detail.release();
      assert.deepEqual(await view(), detailView);
      assert.equal(await page.locator(".message.user").last().textContent(), visibleBefore);
      passed += 1;

      stage = `${viewport.width} 목록 갱신 뒤 새 대화`;
      await newBound();
      const list = defer("GET", "/api/sessions");
      await submit("목록 지연 요청");
      await list.seen;
      await page.locator("#new-session-button").click();
      const blankView = await view();
      await list.release();
      assert.deepEqual(await view(), blankView);
      assert.equal(await page.locator(".message.assistant").count(), 0);
      passed += 1;

      stage = `${viewport.width} 날짜 계산 중 날짜 변경`;
      const runtimeId = (await view()).runtime;
      const day = defer("POST", `/api/runtime/sessions/${runtimeId}/events`);
      await page.locator("#runtime-connect-button").click();
      await day.seen;
      await page.locator("#runtime-target-date").fill("2026-09-06");
      await page.locator("#runtime-target-date").dispatchEvent("change");
      const changedDate = await view();
      await day.release();
      assert.deepEqual(await view(), changedDate);
      assert.equal(changedDate.connected, false);
      assert.equal(await page.locator("#runtime-target-date").inputValue(), "2026-09-06");
      passed += 1;

      stage = `${viewport.width} 원국 생성 중 입력 수정`;
      const creation = defer("POST", "/api/runtime/sessions");
      await page.locator("#runtime-chart-button").click();
      await creation.seen;
      await page.locator("#runtime-birth-date").fill("1994-04-19");
      const editedView = await view(), oldEventCount = runtimeEvents;
      await creation.release();
      assert.deepEqual(await view(), editedView);
      assert.equal(runtimeEvents, oldEventCount);
      passed += 1;

      stage = `${viewport.width} 이전 삭제 응답과 새 원국 보존`;
      await page.locator("#runtime-chart-button").click();
      await page.waitForFunction(() => runtimeChartReady && runtimeOperation === null);
      const beforeDeletion = await view();
      const deletion = defer("DELETE", `/api/runtime/sessions/${beforeDeletion.runtime}`);
      await page.locator("#runtime-delete-button").click();
      await deletion.seen;
      await page.locator("#runtime-birth-date").fill("1994-04-20");
      await page.locator("#runtime-chart-button").click();
      await page.waitForFunction(() => runtimeChartReady && runtimeOperation === null);
      await page.locator("#runtime-chart-connect-button").click();
      const afterRecalculation = await view();
      assert.notEqual(afterRecalculation.runtime, beforeDeletion.runtime);
      await deletion.release();
      assert.deepEqual(await view(), afterRecalculation);
      passed += 1;
      assert.deepEqual(errors, []);
      await page.close();
    }
    process.stdout.write(JSON.stringify({ passed: true, cases_passed: passed, browser_version: browser.version(), synthetic_http: true, model_generations: 0, production_service_accessed: false }));
  } finally {
    await browser.close();
  }
}
main().catch((error) => { process.stderr.write(`제품 후보 합성 화면 검사 실패 (${stage}): ${error.message}\n`); process.exitCode = 1; });
