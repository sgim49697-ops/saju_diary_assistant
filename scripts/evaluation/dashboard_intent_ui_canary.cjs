// dashboard_intent_ui_canary.cjs - 외부 접속 없이 v1.17 실제 화면의 오류 안내를 합성 HTTP로 검사한다.
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(process.env.SAJU_PLAYWRIGHT_MODULE);
const assets = path.resolve(__dirname, "../training/phase5_dashboard_assets/v1.17.0");

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.SAJU_CHROMIUM_EXECUTABLE,
    headless: true,
    args: ["--disable-gpu", "--no-sandbox"],
  });
  let passed = 0;
  try {
    for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
      const page = await browser.newPage({ viewport });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.addInitScript(() => { window.setInterval = () => 0; });
      let code = "RUNTIME_INTENT_CONFIRMATION_REQUIRED";
      let requests = 0;
      await page.route("**/*", async (route) => {
        const url = new URL(route.request().url());
        assert.equal(url.hostname, "saju-canary.invalid");
        if (url.pathname === "/api/generate") {
          requests += 1;
          return route.fulfill({ status: 409, json: { code, error: "합성 확인 안내" } });
        }
        const name = url.pathname === "/" ? "index.html" : url.pathname.slice(1);
        if (["index.html", "dashboard.js", "dashboard.css"].includes(name)) {
          return route.fulfill({
            status: 200,
            contentType: name.endsWith(".js") ? "application/javascript" : name.endsWith(".css") ? "text/css" : "text/html",
            body: fs.readFileSync(path.join(assets, name), "utf8").replace("__CSRF_TOKEN__", "synthetic-csrf"),
          });
        }
        return route.fulfill({ status: 503, json: { error: "합성 화면 검사: 기타 API 미사용" } });
      });
      await page.goto("http://saju-canary.invalid/");
      await page.locator('[data-tab="model"]').click();
      for (const [reason, prefix] of [
        ["RUNTIME_INTENT_CONFIRMATION_REQUIRED", "질문 의도 확인 · 모델 생성 안 함"],
        ["RUNTIME_DATE_REBIND_REQUIRED", "날짜 연결 확인 · 모델 생성 안 함"],
        ["GPU_BUSY", "생성 실패"],
      ]) {
        code = reason;
        const priorRequests = requests;
        await page.evaluate(() => {
          // 모델 가용성 API 대신 표시 상태만 합성한다. 운영 연결 canary가 아니다.
          document.getElementById("manual-panel").classList.remove("hidden");
          connectedRuntimeSessionId = "synthetic-runtime";
          connectedManualSessionId = "synthetic-manual";
          connectedRuntimeDate = "2026-09-05";
          document.getElementById("generate-button").disabled = false;
        });
        await page.locator("#manual-prompt").fill("그럼 내일은 어때?");
        await page.locator("#generate-button").click();
        await page.waitForFunction((expected) => document.getElementById("session-meta").textContent.startsWith(expected), prefix);
        assert.equal(requests, priorRequests + 1);
        assert.equal(await page.locator("#manual-prompt").inputValue(), "그럼 내일은 어때?");
        assert.equal(await page.locator("#generate-button").isEnabled(), true);
        assert.deepEqual(await page.evaluate(() => [connectedRuntimeSessionId, connectedManualSessionId, connectedRuntimeDate]),
          ["synthetic-runtime", "synthetic-manual", "2026-09-05"]);
        passed += 1;
      }
      assert.deepEqual(errors, []);
      await page.close();
    }
    process.stdout.write(JSON.stringify({ passed: true, cases_passed: passed, browser_version: browser.version(), synthetic_http: true, model_generations: 0, production_service_accessed: false }));
  } finally {
    await browser.close();
  }
}
main().catch((error) => { process.stderr.write(`합성 브라우저 검사 실패: ${error.message}\n`); process.exitCode = 1; });
