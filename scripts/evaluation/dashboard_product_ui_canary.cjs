// dashboard_product_ui_canary.cjs - v1.18 실제 자산을 외부 접속·모델 호출 없이 합성 HTTP로 검증한다.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.SAJU_PLAYWRIGHT_MODULE);
const assets = path.resolve(__dirname, "../training/phase5_dashboard_assets/v1.18.0");
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
      const snapshots = { lora_r16: { label: "R16 생성 답변", revision: "synthetic-r16", model_sha256: "a".repeat(64) } };
      const profile = { default_profile: "product_v1", bound_profile: "product_v1", items: [{ profile_id: "product_v1", label: "제품 후보", description: "합성 화면 검증" }] };
      const engines = { default_selection: "lora_r16", selections: [{ selection_id: "lora_r16", label: "R16", mode: "single", engine_ids: ["lora_r16"] }], items: [{ engine_id: "lora_r16", available: true, ...snapshots.lora_r16 }] };
      let session = null;
      const resultFor = (payload) => {
        const text = kind === "model_generated" ? "합성 모델 원출력 <img src=x onerror=alert(1)>" : kind === "direct_fact" ? "연결된 원국의 일간은 병화(丙)입니다." : kind === "clarification" ? "운세인지 일상인지 확인해 주세요." : "요청한 범위는 지원하지 않습니다.";
        const mode = { model_generated: "general", direct_fact: "fact_lookup", clarification: "confirmation", blocked: "unsupported" }[kind];
        const diagnostics = { response_kind: kind, response_mode: mode, notices: [], input_tokens: 50, elapsed_seconds: 0.1, peak_allocated_bytes: 0, grounding_warnings: [] };
        session = {
          session_id: "a".repeat(24), turn_count: (session?.turn_count || 0) + 1,
          updated_at_utc: "2026-09-16T00:00:00Z", prompt_profile: "product_v1", engine_selection: "lora_r16", engine_snapshots: snapshots,
          runtime_binding_sha256: "b".repeat(64), runtime_snapshot_sha256: "c".repeat(64),
          messages: [...(session?.messages || []), { role: "user", content: payload.prompt, created_at_utc: "2026-09-16T00:00:00Z" }, { role: "assistant", engine_id: kind === "model_generated" ? "lora_r16" : null, response_kind: kind, response_mode: mode, content: text, diagnostics, created_at_utc: "2026-09-16T00:00:00Z" }],
        };
        return { session_id: session.session_id, session, output: text, response_kind: kind, runtime_binding_applied: true, contexts: {}, persisted: true, model_calls: 0 };
      };
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        assert.equal(url.hostname, "saju-canary.invalid");
        if (route.request().method() === "DELETE") {
          deletions += 1;
          return route.fulfill({ status: 200, json: { status: "deleted" } });
        }
        if (url.pathname === "/api/generate") {
          generatedRequests += 1;
          latestPayload = route.request().postDataJSON();
          return route.fulfill({ status: 200, json: resultFor(latestPayload) });
        }
        if (url.pathname === "/api/sessions") return route.fulfill({ status: 200, json: { items: session ? [{ ...session, title: "합성 후보 대화", runtime_bound: true }] : [], prompt_profiles: profile, inference_engines: engines } });
        if (url.pathname.startsWith("/api/sessions/") && session) return route.fulfill({ status: 200, json: session });
        if (url.pathname === "/api/runtime/sessions") return route.fulfill({ status: 201, json: { session_id: "d".repeat(24), state_revision: 0 } });
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
      assert.deepEqual(errors, []);
      await page.close();
    }
    process.stdout.write(JSON.stringify({ passed: true, cases_passed: passed, browser_version: browser.version(), synthetic_http: true, model_generations: 0, production_service_accessed: false }));
  } finally {
    await browser.close();
  }
}
main().catch((error) => { process.stderr.write(`제품 후보 합성 화면 검사 실패 (${stage}): ${error.message}\n`); process.exitCode = 1; });
