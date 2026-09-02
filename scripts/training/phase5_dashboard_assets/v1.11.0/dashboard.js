// dashboard.js - KI20 상태 API를 주기적으로 읽어 차트와 모델 검사 화면을 갱신한다.

"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const refreshMilliseconds = 10_000;
let activeTab = "training";
let activeSessionId = null;
let loadedSessionUpdatedAt = null;
let startingNewSession = false;
let selectedPromptProfile = "guided_diagnostic_v1";
let selectedEngineSelection = "ki20_final";
let promptProfileCatalog = null;
let inferenceEngineCatalog = null;
let datasetCatalog = null;
let selectedDatasetSplit = "ki20_train";
let selectedDatasetAxis = "all";
let loadedDatasetKey = null;
let datasetSampleRequestSequence = 0;
let promptExampleCatalog = null;
let selectedPromptExampleCategory = "all";
let runtimeCanaryStatus = null;
let activeRuntimeSessionId = null;
let activeRuntimeRevision = null;
let runtimeChartReady = false;
let runtimeDayReady = false;
let connectedRuntimeSessionId = null;
let connectedManualSessionId = null;
let connectedRuntimeDate = null;
let selectedSessionRuntimeBound = false;

const byId = (id) => document.getElementById(id);
const number = (value, digits = 4) => Number.isFinite(value) ? value.toLocaleString("ko-KR", { maximumFractionDigits: digits }) : "—";
const bytes = (value) => {
  if (!Number.isFinite(value)) return "—";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let current = value;
  let index = 0;
  while (current >= 1024 && index < units.length - 1) { current /= 1024; index += 1; }
  return `${current.toLocaleString("ko-KR", { maximumFractionDigits: 1 })} ${units[index]}`;
};
const localTime = (value) => value ? new Date(value).toLocaleString("ko-KR", { hour12: false }) : "—";
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]);

function validatePromptExampleCatalog(value) {
  if (!value || value.schema_version !== "1.0.0" || value.catalog_id !== "phase5-realistic-saju-manual-v1") {
    throw new Error("질문 예시 카탈로그 식별자가 올바르지 않습니다.");
  }
  if (!value.diagnostic_only || value.formal_gate !== false || value.calculator_connected !== false) {
    throw new Error("질문 예시의 진단 경계가 올바르지 않습니다.");
  }
  if (typeof value.common_preamble !== "string" || !Array.isArray(value.categories) || value.categories.length !== 7 || !Array.isArray(value.items) || value.items.length !== 20) {
    throw new Error("질문 예시는 20개와 분야 목록을 포함해야 합니다.");
  }
  const fixtures = value.fixtures || {};
  const fixtureIds = new Set(Object.keys(fixtures));
  if (!fixtureIds.size || Object.values(fixtures).some((fixture) => typeof fixture?.label !== "string" || typeof fixture?.prompt_text !== "string")) {
    throw new Error("질문 예시의 합성 fixture가 올바르지 않습니다.");
  }
  const categoryIds = new Set();
  value.categories.forEach((category) => {
    if (typeof category?.category_id !== "string" || typeof category?.label !== "string" || !Number.isInteger(category?.expected_items) || category.expected_items < 1 || categoryIds.has(category.category_id)) {
      throw new Error("질문 예시의 분야 계약이 올바르지 않습니다.");
    }
    categoryIds.add(category.category_id);
  });
  if ([...value.categories].reduce((count, category) => count + category.expected_items, 0) !== 20) {
    throw new Error("질문 예시의 분야별 수량이 올바르지 않습니다.");
  }
  const exampleIds = new Set();
  const actualCategoryCounts = new Map(value.categories.map((category) => [category.category_id, 0]));
  value.items.forEach((item) => {
    if (typeof item?.example_id !== "string" || exampleIds.has(item.example_id) || typeof item?.title !== "string" || typeof item?.review_hint !== "string" || !categoryIds.has(item.category) || !Array.isArray(item.turns) || !item.turns.length) {
      throw new Error("질문 예시의 분야 또는 turn이 올바르지 않습니다.");
    }
    exampleIds.add(item.example_id);
    actualCategoryCounts.set(item.category, actualCategoryCounts.get(item.category) + 1);
    item.turns.forEach((turn, turnIndex) => {
      if (turn?.turn !== turnIndex + 1 || typeof turn?.label !== "string" || typeof turn?.question !== "string" || !turn.question.trim() || !Array.isArray(turn.context_refs) || turn.context_refs.some((ref) => !fixtureIds.has(ref)) || typeof turn.same_session_required !== "boolean") {
        throw new Error("질문 예시의 문맥 참조가 올바르지 않습니다.");
      }
      if ((turnIndex === 0 && (!turn.context_refs.length || turn.same_session_required)) || (turnIndex > 0 && (turn.context_refs.length || !turn.same_session_required))) {
        throw new Error("질문 예시의 첫 질문·후속 질문 경계가 올바르지 않습니다.");
      }
      const contexts = turn.context_refs.map((ref) => fixtures[ref].prompt_text.trim());
      const composed = contexts.length ? [value.common_preamble.trim(), ...contexts, `[사용자 질문]\n${turn.question.trim()}`].join("\n\n") : turn.question.trim();
      if (composed.length > 4000) throw new Error("질문 예시가 입력 길이 상한을 넘습니다.");
    });
  });
  if (value.categories.some((category) => actualCategoryCounts.get(category.category_id) !== category.expected_items)) {
    throw new Error("질문 예시의 실제 분야별 수량이 계약과 다릅니다.");
  }
  return value;
}

function composePromptExample(turn) {
  if (!turn.context_refs.length) return turn.question.trim();
  const contexts = turn.context_refs.map((ref) => promptExampleCatalog.fixtures[ref].prompt_text.trim());
  return [promptExampleCatalog.common_preamble.trim(), ...contexts, `[사용자 질문]\n${turn.question.trim()}`].join("\n\n");
}

function fillPromptFromExample(itemIndex, turnIndex) {
  const item = promptExampleCatalog?.items[itemIndex];
  const turn = item?.turns[turnIndex];
  if (!turn) return;
  const textarea = byId("manual-prompt");
  textarea.value = composePromptExample(turn);
  textarea.dispatchEvent(new Event("input", { bubbles: true }));
  textarea.focus({ preventScroll: true });
  textarea.scrollIntoView({ behavior: "smooth", block: "center" });
  const followupWarning = turn.same_session_required && !activeSessionId
    ? " 먼저 이 예시의 첫 질문을 실행해 세션을 만든 뒤 같은 세션에서 사용하세요."
    : "";
  byId("prompt-example-status").textContent = `${item.title} · ${turn.label}을 입력창에 채웠습니다. 아직 모델에는 보내지 않았습니다.${followupWarning}`;
}

function renderPromptExamples() {
  if (!promptExampleCatalog) return;
  const category = selectedPromptExampleCategory;
  const visible = promptExampleCatalog.items
    .map((item, itemIndex) => ({ item, itemIndex }))
    .filter(({ item }) => category === "all" || item.category === category);
  const categoryLabels = new Map(promptExampleCatalog.categories.map((item) => [item.category_id, item.label]));
  const totalTurns = visible.reduce((count, entry) => count + entry.item.turns.length, 0);
  byId("prompt-example-count").textContent = `${visible.length}개 예시 · ${totalTurns}개 질문 버튼`;
  byId("prompt-example-list").innerHTML = visible.map(({ item, itemIndex }) => `<article class="prompt-example-card">
    <header><span class="prompt-example-number">${itemIndex + 1}</span><div><small>${escapeHtml(categoryLabels.get(item.category))}</small><h3>${escapeHtml(item.title)}</h3></div></header>
    <p class="prompt-example-hint">${escapeHtml(item.review_hint)}</p>
    <div class="prompt-example-turns">${item.turns.map((turn, turnIndex) => `<div class="prompt-example-turn">
      <div class="prompt-example-turn-head"><span>${escapeHtml(turn.label)}</span>${turn.same_session_required ? '<b class="followup-badge">같은 세션</b>' : '<b>합성 문맥 포함</b>'}</div>
      <p>${escapeHtml(turn.question)}</p>
      <button class="prompt-example-button ${turn.same_session_required ? "followup" : ""}" type="button" data-example-index="${itemIndex}" data-turn-index="${turnIndex}">${turn.same_session_required ? "후속 질문만 입력창에 넣기" : "합성 명식과 함께 입력창에 넣기"}</button>
    </div>`).join("")}</div>
  </article>`).join("");
  byId("prompt-example-list").querySelectorAll("button[data-example-index]").forEach((button) => button.addEventListener("click", () => {
    fillPromptFromExample(Number(button.dataset.exampleIndex), Number(button.dataset.turnIndex));
  }));
}

async function loadPromptExamples() {
  try {
    const response = await fetch("/prompt-examples.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    promptExampleCatalog = validatePromptExampleCatalog(await response.json());
    byId("prompt-example-category").innerHTML = '<option value="all">전체 20개</option>' + promptExampleCatalog.categories.map((category) => `<option value="${escapeHtml(category.category_id)}">${escapeHtml(category.label)} ${escapeHtml(category.expected_items)}개</option>`).join("");
    renderPromptExamples();
  } catch (error) {
    byId("prompt-example-count").textContent = "카탈로그 로드 실패";
    byId("prompt-example-list").innerHTML = `<p class="empty-conversation">질문 예시를 불러오지 못했습니다: ${escapeHtml(error.message)}</p>`;
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { "X-CSRF-Token": csrfToken, ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `HTTP ${response.status}`);
    error.code = payload.code || `HTTP_${response.status}`;
    error.status = response.status;
    throw error;
  }
  return payload;
}

function runtimeConversationConnected() {
  return Boolean(
    connectedRuntimeSessionId
    && (!activeSessionId || activeSessionId === connectedManualSessionId)
  );
}

function updateRuntimeConversationBinding(message = null) {
  const connected = runtimeConversationConnected();
  const pill = byId("runtime-conversation-binding-pill");
  pill.textContent = connected ? `원국·${connectedRuntimeDate} 연결됨` : "원국·날짜 대화 미연결";
  pill.className = `status-pill ${connected ? "connected" : "locked"}`;
  const connectButton = byId("runtime-connect-button");
  connectButton.disabled = !runtimeChartReady || !activeRuntimeSessionId || connected;
  connectButton.textContent = connected ? "이 원국·날짜로 대화 중" : "이 원국·날짜로 새 대화 시작";
  const unavailableBoundSession = selectedSessionRuntimeBound && !connected;
  byId("manual-binding-copy").textContent = message || (
    connected
      ? `계산한 승인 원국과 ${connectedRuntimeDate} 일진이 이 새 대화에 고정됐습니다. 모델 응답은 자동 grounding 검사를 통과해야 저장됩니다.`
      : unavailableBoundSession
        ? "이 원국·날짜 대화의 암호화 capability가 현재 페이지에 없습니다. 원국을 다시 계산하고 새 연결 대화를 시작하세요."
        : "일반 대화입니다. 계산한 원국과 날짜는 위 연결 버튼을 눌러 새 대화에만 결합합니다."
  );
  const generateButton = byId("generate-button");
  const prompt = byId("manual-prompt");
  if (unavailableBoundSession) {
    generateButton.disabled = true;
    prompt.disabled = true;
  } else if (generateButton.textContent !== "모델 로딩 중…") {
    generateButton.disabled = false;
    prompt.disabled = false;
  }
}

function disconnectRuntimeConversation(message = null) {
  connectedRuntimeSessionId = null;
  connectedManualSessionId = null;
  connectedRuntimeDate = null;
  updateRuntimeConversationBinding(message);
}

function setRuntimeFormEnabled(enabled) {
  byId("runtime-chart-form").querySelectorAll("input, select, button").forEach((field) => { field.disabled = !enabled; });
}

function renderRuntimeStatus(status) {
  runtimeCanaryStatus = status;
  const panel = byId("runtime-panel");
  panel.classList.toggle("hidden", !status.configured);
  if (!status.configured) return;
  const pill = byId("runtime-status-pill");
  pill.textContent = status.enabled ? "활성" : status.release_available ? "기본 OFF" : "Gate 대기";
  pill.className = `status-pill ${status.enabled ? "" : status.release_available ? "locked" : "incomplete"}`.trim();
  byId("runtime-status-copy").textContent = status.message;
  byId("runtime-remote-warning").classList.toggle("hidden", !status.remote_unauthenticated);
  setRuntimeFormEnabled(status.enabled);
  const targetDate = byId("runtime-target-date");
  if (status.single_day_minimum) targetDate.min = status.single_day_minimum;
  if (status.single_day_maximum) targetDate.max = status.single_day_maximum;
  if (status.single_day_today_kst && !targetDate.value) targetDate.value = status.single_day_today_kst;
  targetDate.disabled = !status.enabled;
  byId("runtime-today-button").disabled = !status.enabled;
  byId("runtime-day-note").textContent = status.single_day_minimum && status.single_day_maximum
    ? `서버 KST 기준 ${status.single_day_minimum}~${status.single_day_maximum}, 12:00 기준 단일 일진만 연결합니다.`
    : "서버 KST 기준 오늘부터 2049-12-31까지, 12:00 기준 단일 일진만 연결합니다.";
  if (!status.enabled) {
    runtimeChartReady = false;
    runtimeDayReady = false;
    disconnectRuntimeConversation("원국·단일 일진 runtime이 비활성화되어 대화 연결을 해제했습니다.");
  }
  byId("diagnostic-engine-note").textContent = status.enabled
    ? "원국·날짜 연결 뒤 K0와 KI20 비교를 선택하면 두 모델에 동일한 canonical 사실 snapshot을 전달합니다. 모델 해석은 품질 승격 근거가 아닙니다."
    : "K0와 KI20 모두 계산 엔진이 연결되지 않은 진단용 언어 모델입니다. 출력은 명리학적 정답이나 운영 승격 근거가 아닙니다.";
}

async function refreshRuntimeStatus() {
  try {
    renderRuntimeStatus(await api("/api/runtime/status"));
  } catch (error) {
    runtimeCanaryStatus = null;
    byId("runtime-panel").classList.add("hidden");
  }
}

function updateRuntimeTimeFields() {
  const precision = byId("runtime-time-precision").value;
  byId("runtime-exact-time-field").classList.toggle("hidden", precision !== "exact");
  byId("runtime-range-start-field").classList.toggle("hidden", precision !== "range");
  byId("runtime-range-end-field").classList.toggle("hidden", precision !== "range");
  byId("runtime-birth-time").required = precision === "exact";
  byId("runtime-range-start").required = precision === "range";
  byId("runtime-range-end").required = precision === "range";
}

function updateRuntimeCalendarFields() {
  byId("runtime-leap-field").classList.toggle("hidden", byId("runtime-calendar").value !== "lunar");
}

function renderRuntimeFacts(payload) {
  if (!payload?.result) return;
  activeRuntimeRevision = payload.state_revision;
  const chart = payload.result.chart || payload.result;
  const period = payload.result.period || null;
  runtimeChartReady = chart?.status === "ok" && chart?.fact_authority === "HARD_GT";
  runtimeDayReady = period?.status === "ok" && period?.fact_authority === "HARD_GT";
  byId("runtime-facts-panel").classList.remove("hidden");
  byId("runtime-revision").textContent = `암호화 세션 · rev ${payload.state_revision}`;
  byId("runtime-facts-json").textContent = JSON.stringify(payload.result, null, 2);
  updateRuntimeConversationBinding();
}

function renderAlerts(alerts) {
  const target = byId("alerts");
  if (!alerts.length) {
    target.innerHTML = '<div class="alert good">현재 runtime hard alert가 없습니다. Loss 추세는 품질 Gate가 아닌 진단값입니다.</div>';
    return;
  }
  target.innerHTML = alerts.map((alert) => `<div class="alert ${escapeHtml(alert.level)}"><strong>${escapeHtml(alert.code)}</strong> · ${escapeHtml(alert.message)}</div>`).join("");
}

function renderStatus(status) {
  const running = status.run.lifecycle === "running";
  const failed = ["failed", "interrupted", "stopped_unexpectedly"].includes(status.run.lifecycle);
  byId("live-dot").className = `live-dot ${running ? "running" : failed ? "error" : ""}`;
  byId("lifecycle").textContent = ({ running: "학습 실행 중", complete: "학습 완료", failed: "학습 실패", interrupted: "학습 중단", stopped_unexpectedly: "예상 밖 중지" })[status.run.lifecycle] || status.run.lifecycle;
  byId("last-refresh").textContent = `최근 갱신 ${localTime(status.refreshed_at_utc)}`;
  byId("progress-value").textContent = `${number(status.progress.percent, 1)}%`;
  byId("step-value").textContent = `${number(status.progress.global_step, 0)} / ${number(status.progress.expected_optimizer_steps, 0)} step`;
  byId("epoch-value").textContent = `epoch ${number(status.progress.epoch, 3)}`;
  byId("eta-value").textContent = `예상 종료 ${localTime(status.progress.estimated_finish_at_utc)}`;
  byId("progress-bar").style.width = `${Math.min(100, Math.max(0, status.progress.percent))}%`;
  document.querySelector(".progress-track").setAttribute("aria-valuenow", String(status.progress.percent));
  byId("train-loss").textContent = number(status.latest_train?.loss);
  byId("train-accuracy").textContent = `token accuracy ${number((status.latest_train?.mean_token_accuracy ?? NaN) * 100, 2)}% · ${number(status.latest_train?.num_tokens, 0)} tokens`;
  byId("eval-loss").textContent = number(status.latest_eval?.eval_loss);
  byId("eval-accuracy").textContent = `token accuracy ${number((status.latest_eval?.eval_mean_token_accuracy ?? NaN) * 100, 2)}%`;
  byId("grad-norm").textContent = number(status.latest_train?.grad_norm);
  byId("learning-rate").textContent = `learning rate ${Number.isFinite(status.latest_train?.learning_rate) ? status.latest_train.learning_rate.toExponential(3) : "—"}`;
  byId("gpu-used").textContent = status.gpu.available ? `${number(status.gpu.used_mib, 0)} MiB` : "측정 불가";
  byId("gpu-detail").textContent = status.gpu.available ? `${status.gpu.name} · ${number(status.gpu.used_mib * 100 / 16384, 1)}% of cap` : status.gpu.error;
  byId("run-identity").textContent = `${status.run.run_build_id} · ${status.run.run_sha256.slice(0, 16)}…`;
  byId("service-identity").textContent = `${status.service.unit || "—"} · PID ${status.service.main_pid || "—"}`;
  renderAlerts(status.alerts);
}

function pointsFor(rows, field, multiplier = 1) {
  return rows.filter((row) => Number.isFinite(row[field]) && Number.isFinite(row.global_step)).map((row) => ({ x: row.global_step, y: row[field] * multiplier }));
}

function linePath(points, xScale, yScale) {
  return points.map((point, index) => `${index ? "L" : "M"}${xScale(point.x).toFixed(2)},${yScale(point.y).toFixed(2)}`).join(" ");
}

function renderChart(targetId, series, options = {}) {
  const target = byId(targetId);
  const all = series.flatMap((item) => item.points);
  if (!all.length) { target.innerHTML = '<p class="footnote">표시할 지표가 아직 없습니다.</p>'; return; }
  const width = 680;
  const height = 235;
  const margin = { top: 10, right: 16, bottom: 28, left: 52 };
  const xMax = Math.max(...all.map((point) => point.x), 1);
  const rawMin = Math.min(...all.map((point) => point.y));
  const rawMax = Math.max(...all.map((point) => point.y));
  const yMin = options.zero ? 0 : Math.min(rawMin, options.min ?? rawMin);
  const yMax = Math.max(rawMax, options.max ?? rawMax, yMin + 1e-9);
  const xScale = (value) => margin.left + value / xMax * (width - margin.left - margin.right);
  const yScale = (value) => margin.top + (yMax - value) / (yMax - yMin) * (height - margin.top - margin.bottom);
  const grid = [0, 0.25, 0.5, 0.75, 1].map((ratio) => {
    const y = margin.top + ratio * (height - margin.top - margin.bottom);
    const label = yMax - ratio * (yMax - yMin);
    return `<line class="grid-line" x1="${margin.left}" x2="${width - margin.right}" y1="${y}" y2="${y}"/><text class="axis-copy" x="${margin.left - 8}" y="${y + 3}" text-anchor="end">${number(label, options.digits ?? 2)}</text>`;
  }).join("");
  const lines = series.map((item) => `<path class="${item.className}" d="${linePath(item.points, xScale, yScale)}"/>`).join("");
  const xLabels = [0, 0.5, 1].map((ratio) => `<text class="axis-copy" x="${margin.left + ratio * (width - margin.left - margin.right)}" y="${height - 5}" text-anchor="middle">${number(xMax * ratio, 0)}</text>`).join("");
  target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img">${grid}${lines}${xLabels}</svg>`;
}

function renderMetrics(metrics) {
  renderChart("loss-chart", [
    { className: "train-line", points: pointsFor(metrics.train, "loss") },
    { className: "eval-line", points: pointsFor(metrics.evaluation, "eval_loss") },
  ], { zero: true });
  renderChart("accuracy-chart", [
    { className: "train-line", points: pointsFor(metrics.train, "mean_token_accuracy", 100) },
    { className: "eval-line", points: pointsFor(metrics.evaluation, "eval_mean_token_accuracy", 100) },
  ], { min: 0, max: 100, digits: 0 });
  renderChart("optimizer-chart", [
    { className: "train-line", points: pointsFor(metrics.train, "grad_norm") },
    { className: "eval-line", points: pointsFor(metrics.train, "learning_rate", 1_000_000) },
  ], { zero: true });
  renderChart("gpu-chart", [
    { className: "train-line", points: pointsFor(metrics.train, "gpu_total_memory_used_mib") },
    { className: "eval-line", points: metrics.train.length ? [{ x: 1, y: 16384 }, { x: metrics.train.at(-1).global_step, y: 16384 }] : [] },
  ], { zero: true, max: 16384, digits: 0 });
  renderChart("entropy-chart", [{ className: "train-line", points: pointsFor(metrics.train, "entropy") }], { zero: true });
}

function renderCheckpoints(payload) {
  byId("disk-free").textContent = `남은 디스크 ${bytes(payload.disk_free_bytes)}`;
  const body = byId("checkpoint-rows");
  if (!payload.items.length) { body.innerHTML = '<tr><td colspan="5">저장된 체크포인트가 없습니다.</td></tr>'; return; }
  body.innerHTML = payload.items.map((item) => `<tr>
    <td><code>${escapeHtml(item.name)}</code></td>
    <td><span class="status-pill ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>${item.missing_files.length ? `<small title="${escapeHtml(item.missing_files.join(", "))}"> · ${item.missing_files.length}개 누락</small>` : ""}</td>
    <td>${bytes(item.size_bytes)}</td>
    <td>${localTime(item.modified_at_utc)}</td>
    <td>${item.preserved_milestone ? "milestone" : item.name === "final" ? "final" : "rotation"}</td>
  </tr>`).join("");
}

function promptText(messages) {
  return (messages || []).map((message) => `[${message.role}] ${message.content}`).join("\n\n");
}

function metricHeadline(score) {
  const metrics = score?.metrics || {};
  return `${number(metrics.parseable_nonempty_percent, 1)}% non-empty · 사실 위반 ${number(metrics.input_fact_violation?.violations, 0)}`;
}

function renderModelChecks(payload) {
  const gate = payload.generation_gate;
  const lock = byId("model-lock");
  const unlocked = gate.allowed;
  lock.classList.toggle("unlocked", unlocked);
  lock.querySelector("h2").textContent = unlocked ? "final 모델 검사 사용 가능" : "학습 중에는 모델을 추가로 로드하지 않습니다.";
  byId("model-lock-copy").textContent = unlocked ? "GPU가 비어 있으며 학습 서비스가 종료됐습니다." : `차단 조건: ${gate.reasons.join(", ") || "확인 중"}`;
  byId("manual-panel").classList.toggle("hidden", !unlocked);
  byId("prompt-example-panel").classList.toggle("hidden", !unlocked);
  byId("run-probe-button").classList.toggle("hidden", !unlocked || payload.status === "available");
  byId("comparison-panel").classList.toggle("hidden", payload.status !== "available");
  if (payload.status !== "available") return;
  byId("comparison-status").textContent = "정식 Gate 아님";
  byId("comparison-summary").innerHTML = `
    <div class="score-box"><span class="label">KI10</span><strong>${escapeHtml(metricHeadline(payload.summary.ki10_diagnostic))}</strong></div>
    <div class="score-box"><span class="label">KI20</span><strong>${escapeHtml(metricHeadline(payload.summary.ki20_diagnostic))}</strong></div>`;
  byId("comparison-glance").textContent = `KI10 ${metricHeadline(payload.summary.ki10_diagnostic)} · KI20 ${metricHeadline(payload.summary.ki20_diagnostic)}`;
  byId("comparison-list").innerHTML = payload.rows.map((row, index) => `<details class="comparison-item">
    <summary>${index + 1}. ${escapeHtml(row.category)} · ${escapeHtml(row.eval_id)}</summary>
    <div class="comparison-body">
      <div class="comparison-prompt"><span class="label">PROMPT</span><p>${escapeHtml(promptText(row.prompt_messages))}</p></div>
      <div class="comparison-column"><span class="label">KI10</span><p>${escapeHtml(row.ki10_output)}</p></div>
      <div class="comparison-column"><span class="label">KI20</span><p>${escapeHtml(row.ki20_output)}</p></div>
    </div>
  </details>`).join("");
}

function selectedSplit() {
  return datasetCatalog?.splits.find((split) => split.split_id === selectedDatasetSplit) || null;
}

function renderSplitCards() {
  const target = byId("split-cards");
  const cards = datasetCatalog.splits.map((split) => `<button type="button" class="split-card ${split.split_id === selectedDatasetSplit ? "selected" : ""}" data-split-id="${escapeHtml(split.split_id)}" aria-pressed="${split.split_id === selectedDatasetSplit}">
    <span class="split-kind">${escapeHtml(split.kind)}</span>
    <strong>${escapeHtml(split.label)}</strong>
    <b>${number(split.rows, 0)}행</b>
    <small>${escapeHtml(split.role)}</small>
  </button>`).join("");
  const blind = datasetCatalog.sealed_blind;
  target.innerHTML = cards + `<article class="split-card sealed-card">
    <span class="split-kind">sealed</span>
    <strong>${escapeHtml(blind.label)}</strong>
    <b>${number(blind.rows, 0)}행 · ${number(blind.components, 0)} component</b>
    <small>${escapeHtml(blind.role)} · 샘플 열람 불가</small>
  </article>`;
  target.querySelectorAll("button[data-split-id]").forEach((button) => button.addEventListener("click", () => {
    selectDatasetSplit(button.dataset.splitId);
  }));
}

function renderAxisDistribution() {
  const split = selectedSplit();
  if (!split) return;
  byId("dataset-detail-title").textContent = `${split.label} 구성`;
  byId("dataset-total").textContent = `${number(split.rows, 0)}행`;
  const axes = [{ axis: "all", label: "전체 혼합", rows: split.rows, percent: 100, restricted_local_only: false }, ...split.axes];
  const target = byId("axis-distribution");
  target.innerHTML = axes.map((axis, index) => `<button type="button" class="axis-row ${axis.axis === selectedDatasetAxis ? "selected" : ""} ${axis.axis === "all" ? "all-axis" : ""}" data-axis-id="${escapeHtml(axis.axis)}" aria-pressed="${axis.axis === selectedDatasetAxis}">
    <div class="axis-copy"><strong>${escapeHtml(axis.label)}</strong><span>${number(axis.rows, 0)}행 · ${number(axis.percent, 2)}%${axis.restricted_local_only ? " · 제한" : ""}</span></div>
    <div class="axis-track"><div class="axis-fill axis-color-${index % 5}" style="width:${Math.max(1, axis.percent)}%"></div></div>
  </button>`).join("");
  target.querySelectorAll("button[data-axis-id]").forEach((button) => button.addEventListener("click", () => {
    selectDatasetAxis(button.dataset.axisId);
  }));
}

function renderDatasetSampleHeading() {
  const split = selectedSplit();
  if (!split) return;
  const axis = selectedDatasetAxis === "all"
    ? { label: "전체 혼합", rows: split.rows }
    : split.axes.find((item) => item.axis === selectedDatasetAxis);
  if (!axis) return;
  byId("dataset-sample-title").textContent = `${split.label} / ${axis.label}`;
  byId("dataset-sample-meta").textContent = `${number(axis.rows, 0)}개 후보에서 매 요청 10건을 독립적으로 추출합니다.`;
}

function sampleMessages(messages) {
  return (messages || []).map((message) => `<div class="sample-message ${escapeHtml(message.role)}">
    <span>${escapeHtml(({ system: "SYSTEM", user: "USER", assistant: "ASSISTANT" })[message.role] || message.role)}</span>
    <p>${escapeHtml(message.content)}</p>
  </div>`).join("");
}

function samplePreview(item) {
  if (item.format === "messages") {
    const message = item.messages?.find((entry) => entry.role === "user") || item.messages?.[0];
    return message?.content || "대화 미리보기가 없습니다.";
  }
  return JSON.stringify(item.input ?? {}, null, 0);
}

function renderDatasetSamples(payload) {
  if (payload.items.length !== 10 || payload.selection?.mode !== "cryptographic_random" || payload.selection?.returned !== 10) {
    throw new Error("무작위 샘플 응답이 10건 계약과 다릅니다.");
  }
  byId("dataset-restriction").classList.toggle("hidden", !payload.restricted_content_included);
  byId("dataset-samples").innerHTML = payload.items.map((item, index) => {
    const body = item.format === "messages"
      ? sampleMessages(item.messages)
      : `<div class="structured-sample"><div><span>INPUT</span><pre>${escapeHtml(JSON.stringify(item.input, null, 2))}</pre></div><div><span>EXPECTED</span><pre>${escapeHtml(JSON.stringify(item.expected, null, 2))}</pre></div></div>`;
    return `<details class="dataset-sample-card">
      <summary class="dataset-sample-summary">
        <span class="sample-number">${index + 1}</span>
        <div class="dataset-sample-preview"><strong>${escapeHtml(item.axis_label)}</strong><p>${escapeHtml(samplePreview(item))}</p></div>
        <div class="sample-badges"><span>${escapeHtml(item.task || "sample")}</span>${item.restricted_local_only ? '<span class="restricted-badge">로컬 제한</span>' : ""}</div>
      </summary>
      <div class="dataset-sample-body">${body}<footer><code>${escapeHtml(item.sample_key)}</code>${item.reference_available === false ? " · reference 없음" : ""}</footer></div>
    </details>`;
  }).join("");
  byId("dataset-random-status").textContent = "무작위 10건 · 요청 간 반복 가능";
}

async function loadDatasetSamples({ force = false } = {}) {
  const key = `${selectedDatasetSplit}/${selectedDatasetAxis}`;
  if (!force && loadedDatasetKey === key) return;
  const sequence = ++datasetSampleRequestSequence;
  const preservesCurrentSamples = force && loadedDatasetKey === key;
  const button = byId("random-dataset-samples-button");
  button.disabled = true;
  button.textContent = "무작위 10건 추출 중…";
  byId("dataset-random-status").textContent = "후보 검증·추출 중";
  if (!preservesCurrentSamples) {
    byId("dataset-samples").innerHTML = '<p class="empty-conversation">검증된 후보 풀에서 무작위 10건을 불러오는 중입니다.</p>';
    byId("dataset-restriction").classList.add("hidden");
  }
  try {
    const payload = await api(`/api/dataset-samples/${selectedDatasetSplit}/${selectedDatasetAxis}/random`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (sequence !== datasetSampleRequestSequence || `${selectedDatasetSplit}/${selectedDatasetAxis}` !== key) return;
    renderDatasetSamples(payload);
    loadedDatasetKey = key;
  } catch (error) {
    if (sequence !== datasetSampleRequestSequence || `${selectedDatasetSplit}/${selectedDatasetAxis}` !== key) return;
    byId("dataset-random-status").textContent = `추출 실패 · ${error.message}`;
    if (!preservesCurrentSamples) {
      loadedDatasetKey = null;
      byId("dataset-samples").innerHTML = `<p class="empty-conversation">샘플 로드 실패: ${escapeHtml(error.message)}</p>`;
    }
  } finally {
    if (sequence === datasetSampleRequestSequence) {
      button.disabled = false;
      button.textContent = "다른 10개 보기";
    }
  }
}

function selectDatasetSplit(splitId) {
  if (!datasetCatalog?.splits.some((split) => split.split_id === splitId)) return;
  if (selectedDatasetSplit === splitId) return;
  selectedDatasetSplit = splitId;
  selectedDatasetAxis = "all";
  loadedDatasetKey = null;
  renderSplitCards();
  renderAxisDistribution();
  renderDatasetSampleHeading();
  loadDatasetSamples();
}

function selectDatasetAxis(axisId) {
  const split = selectedSplit();
  if (!split || (axisId !== "all" && !split.axes.some((axis) => axis.axis === axisId))) return;
  if (selectedDatasetAxis === axisId) return;
  selectedDatasetAxis = axisId;
  loadedDatasetKey = null;
  renderAxisDistribution();
  renderDatasetSampleHeading();
  loadDatasetSamples();
}

function renderDatasetCatalog(payload) {
  datasetCatalog = payload;
  if (!payload.splits.some((split) => split.split_id === selectedDatasetSplit)) selectedDatasetSplit = payload.splits[0].split_id;
  const split = selectedSplit();
  if (selectedDatasetAxis !== "all" && !split.axes.some((axis) => axis.axis === selectedDatasetAxis)) selectedDatasetAxis = "all";
  renderSplitCards();
  renderAxisDistribution();
  renderDatasetSampleHeading();
  loadDatasetSamples();
}

function sessionTurns(session) {
  const turns = [];
  for (const message of session.messages) {
    if (message.role === "user") {
      turns.push({ user: message, assistants: [] });
    } else if (turns.length) {
      turns[turns.length - 1].assistants.push(message);
    }
  }
  return turns;
}

function engineSnapshot(session, engineId) {
  return session.engine_snapshots?.[engineId]
    || inferenceEngineCatalog?.items?.find((engine) => engine.engine_id === engineId)
    || { engine_id: engineId, label: engineId, revision: "unknown", model_sha256: "" };
}

function renderDiagnostics(diagnostics) {
  if (!diagnostics) return "";
  const gpu = Number.isFinite(diagnostics.gpu_total_memory_used_mib) ? ` · GPU ${number(diagnostics.gpu_total_memory_used_mib, 0)} MiB` : "";
  return `<small class="message-diagnostics">${number(diagnostics.input_tokens, 0)} input tokens · ${number(diagnostics.elapsed_seconds, 1)}초 · peak ${bytes(diagnostics.peak_allocated_bytes)}${gpu}</small>`;
}

function renderConversation(session, contexts = null) {
  const target = byId("manual-conversation");
  if (!session) {
    target.innerHTML = '<p class="empty-conversation">새 세션입니다. 첫 질문을 입력하세요.</p>';
    byId("session-meta").textContent = "새 세션에서 첫 질문을 입력하세요.";
    return;
  }
  target.innerHTML = sessionTurns(session).map((turn) => {
    const assistants = turn.assistants.map((message) => {
      const engineId = message.engine_id || "ki20_final";
      const engine = engineSnapshot(session, engineId);
      const fingerprint = `${engine.revision} · ${(engine.model_sha256 || "").slice(0, 12)}…`;
      return `<article class="message assistant engine-${escapeHtml(engineId.replaceAll("_", "-"))}">
        <div class="message-head"><strong>${escapeHtml(engine.label)}</strong><span>${escapeHtml(localTime(message.created_at_utc))}</span></div>
        <p>${escapeHtml(message.content)}</p>
        <small class="message-diagnostics">${escapeHtml(fingerprint)}</small>
        ${renderDiagnostics(message.diagnostics)}
      </article>`;
    }).join("");
    const paired = turn.assistants.length > 1 ? " paired" : "";
    return `<section class="conversation-turn">
      <article class="message user">
        <div class="message-head"><strong>사용자</strong><span>${escapeHtml(localTime(turn.user.created_at_utc))}</span></div>
        <p>${escapeHtml(turn.user.content)}</p>
      </article>
      <div class="assistant-response-grid${paired}">${assistants}</div>
    </section>`;
  }).join("");
  const omittedCount = Math.max(0, ...Object.values(contexts || {}).map((item) => item.omitted_turns || 0));
  const omitted = omittedCount ? ` · 오래된 ${omittedCount} turn 입력 제외` : "";
  const profile = session.prompt_profile_label || session.prompt_profile || "기존 무지시";
  const engine = session.engine_selection_label || "KI20 단독";
  byId("session-meta").textContent = `${session.turn_count} turn · ${engine} · ${profile} · 최근 저장 ${localTime(session.updated_at_utc)}${omitted}`;
  target.scrollTop = target.scrollHeight;
}

function engineSelectionMetadata(selectionId) {
  return inferenceEngineCatalog?.selections?.find((selection) => selection.selection_id === selectionId) || null;
}

function renderEngineSelection(selectionId, locked) {
  const select = byId("engine-selection-select");
  const selections = inferenceEngineCatalog?.selections || [];
  const engines = new Map((inferenceEngineCatalog?.items || []).map((engine) => [engine.engine_id, engine]));
  select.innerHTML = selections.map((selection) => {
    const available = selection.engine_ids.every((engineId) => engines.get(engineId)?.available);
    return `<option value="${escapeHtml(selection.selection_id)}"${available ? "" : " disabled"}>${escapeHtml(selection.label)}${available ? "" : " · 사용 불가"}</option>`;
  }).join("");
  selectedEngineSelection = selectionId;
  select.value = selectionId;
  select.disabled = locked;
  const metadata = engineSelectionMetadata(selectionId);
  const identities = (metadata?.engine_ids || []).map((engineId) => {
    const engine = engines.get(engineId);
    return engine ? `${engine.label} ${engine.revision.slice(0, 12)}…` : engineId;
  }).join(" / ");
  const lockCopy = locked ? " 이 세션에서는 변경할 수 없습니다." : " 첫 응답 뒤에는 변경할 수 없습니다.";
  byId("engine-selection-copy").textContent = `${metadata?.mode === "paired" ? "같은 질문을 독립 문맥으로 순차 비교합니다." : "선택한 모델만 로드합니다."} ${identities}.${lockCopy}`;
  byId("manual-prompt-label").textContent = metadata?.mode === "paired" ? "K0와 KI20에 함께 보낼 질문" : `${metadata?.label || "선택 모델"}에 보낼 질문`;
  if (!byId("generate-button").disabled) byId("generate-button").textContent = metadata?.mode === "paired" ? "두 모델 비교" : "답변 생성";
}

function profileMetadata(profileId) {
  const item = promptProfileCatalog?.items?.find((profile) => profile.profile_id === profileId);
  if (item) return item;
  if (profileId === promptProfileCatalog?.legacy_profile || profileId === "raw_legacy") {
    return { profile_id: "raw_legacy", label: "기존 무지시", description: "과거 세션의 system message 없는 원출력입니다.", diagnostic_only: true };
  }
  return null;
}

function renderPromptProfile(profileId, locked) {
  const select = byId("prompt-profile-select");
  const items = [...(promptProfileCatalog?.items || [])];
  if (profileId === promptProfileCatalog?.legacy_profile || profileId === "raw_legacy") {
    items.push({ profile_id: "raw_legacy", label: "기존 무지시", description: "과거 세션의 system message 없는 원출력입니다.", diagnostic_only: true });
  }
  select.innerHTML = items.map((item) => {
    const boundOnly = item.profile_id === promptProfileCatalog?.bound_profile;
    const disabled = boundOnly && !runtimeConversationConnected() && profileId !== item.profile_id;
    return `<option value="${escapeHtml(item.profile_id)}"${disabled ? " disabled" : ""}>${escapeHtml(item.label)}${item.diagnostic_only ? " · 진단" : " · 원국 연결"}</option>`;
  }).join("");
  selectedPromptProfile = profileId;
  select.value = profileId;
  select.disabled = locked;
  const metadata = profileMetadata(profileId);
  byId("prompt-profile-copy").textContent = `${metadata?.description || "프로필 정보를 불러오는 중입니다."}${locked ? " 이 세션에서는 변경할 수 없습니다." : " 새 세션을 만든 뒤에는 변경할 수 없습니다."}`;
}

async function renderSessions(payload) {
  promptProfileCatalog = payload.prompt_profiles;
  inferenceEngineCatalog = payload.inference_engines;
  const select = byId("session-select");
  const knownIds = new Set(payload.items.map((item) => item.session_id));
  if (activeSessionId && !knownIds.has(activeSessionId)) {
    activeSessionId = null;
    loadedSessionUpdatedAt = null;
  }
  if (!activeSessionId && !startingNewSession && payload.items.length) {
    activeSessionId = payload.items[0].session_id;
  }
  select.innerHTML = '<option value="">새 대화</option>' + payload.items.map((item) =>
    `<option value="${escapeHtml(item.session_id)}">[${escapeHtml(item.engine_selection_label)}] ${escapeHtml(item.title)} · ${item.turn_count} turn</option>`
  ).join("");
  select.value = activeSessionId || "";
  if (!activeSessionId) {
    selectedSessionRuntimeBound = false;
    const requested = profileMetadata(selectedPromptProfile) ? selectedPromptProfile : payload.prompt_profiles.default_profile;
    const requestedEngine = engineSelectionMetadata(selectedEngineSelection) ? selectedEngineSelection : payload.inference_engines.default_selection;
    renderPromptProfile(requested, false);
    renderEngineSelection(requestedEngine, false);
    if (loadedSessionUpdatedAt !== null || startingNewSession || !payload.items.length) {
      loadedSessionUpdatedAt = null;
      renderConversation(null);
    }
    updateRuntimeConversationBinding();
    return;
  }
  const summary = payload.items.find((item) => item.session_id === activeSessionId);
  selectedSessionRuntimeBound = Boolean(summary?.runtime_bound);
  if (summary) {
    renderPromptProfile(summary.prompt_profile, true);
    renderEngineSelection(summary.engine_selection, true);
  }
  if (summary && summary.updated_at_utc !== loadedSessionUpdatedAt) {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderConversation(session);
  }
  updateRuntimeConversationBinding();
}

async function refresh() {
  const runtimeRefresh = refreshRuntimeStatus();
  try {
    const [status, metrics, checkpoints, checks, sessions, datasets] = await Promise.all([
      api("/api/status"), api("/api/metrics"), api("/api/checkpoints"), api("/api/model-checks"), api("/api/sessions"), api("/api/dataset-splits"),
    ]);
    renderStatus(status);
    renderMetrics(metrics);
    renderCheckpoints(checkpoints);
    renderModelChecks(checks);
    await renderSessions(sessions);
    renderDatasetCatalog(datasets);
    await runtimeRefresh;
  } catch (error) {
    byId("live-dot").className = "live-dot error";
    byId("lifecycle").textContent = "대시보드 오류";
    byId("last-refresh").textContent = error.message;
    renderAlerts([{ level: "critical", code: "dashboard_fetch", message: error.message }]);
  }
}

document.querySelectorAll(".tab").forEach((button) => button.addEventListener("click", () => {
  activeTab = button.dataset.tab;
  document.querySelectorAll(".tab").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.toggle("active", panel.id === `tab-${activeTab}`));
}));

byId("new-session-button").addEventListener("click", () => {
  activeSessionId = null;
  loadedSessionUpdatedAt = null;
  startingNewSession = true;
  selectedSessionRuntimeBound = false;
  disconnectRuntimeConversation("새 일반 대화를 시작했습니다. 계산한 원국을 쓰려면 위 연결 버튼을 누르세요.");
  byId("session-select").value = "";
  byId("manual-prompt").value = "";
  selectedPromptProfile = promptProfileCatalog?.default_profile || "guided_diagnostic_v1";
  selectedEngineSelection = inferenceEngineCatalog?.default_selection || "ki20_final";
  renderPromptProfile(selectedPromptProfile, false);
  renderEngineSelection(selectedEngineSelection, false);
  renderConversation(null);
  byId("manual-prompt").focus();
});

byId("session-select").addEventListener("change", async (event) => {
  const nextSessionId = event.target.value || null;
  if (nextSessionId !== connectedManualSessionId) {
    disconnectRuntimeConversation();
  }
  activeSessionId = nextSessionId;
  loadedSessionUpdatedAt = null;
  startingNewSession = activeSessionId === null;
  if (!activeSessionId) {
    selectedSessionRuntimeBound = false;
    selectedPromptProfile = promptProfileCatalog?.default_profile || "guided_diagnostic_v1";
    selectedEngineSelection = inferenceEngineCatalog?.default_selection || "ki20_final";
    renderPromptProfile(selectedPromptProfile, false);
    renderEngineSelection(selectedEngineSelection, false);
    renderConversation(null);
    updateRuntimeConversationBinding();
    return;
  }
  try {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderPromptProfile(session.prompt_profile, true);
    renderEngineSelection(session.engine_selection, true);
    selectedSessionRuntimeBound = Boolean(session.runtime_binding_sha256 || session.runtime_session_id);
    renderConversation(session);
    updateRuntimeConversationBinding();
  } catch (error) {
    byId("session-meta").textContent = `세션 로드 실패: ${error.message}`;
  }
});

byId("prompt-profile-select").addEventListener("change", (event) => {
  if (activeSessionId) return;
  selectedPromptProfile = event.target.value;
  renderPromptProfile(selectedPromptProfile, false);
});

byId("engine-selection-select").addEventListener("change", (event) => {
  if (activeSessionId) return;
  selectedEngineSelection = event.target.value;
  renderEngineSelection(selectedEngineSelection, false);
});

byId("random-dataset-samples-button").addEventListener("click", () => {
  loadDatasetSamples({ force: true });
});

byId("prompt-example-category").addEventListener("change", (event) => {
  selectedPromptExampleCategory = event.target.value;
  renderPromptExamples();
});

byId("runtime-calendar").addEventListener("change", updateRuntimeCalendarFields);
byId("runtime-time-precision").addEventListener("change", updateRuntimeTimeFields);
byId("runtime-chart-form").addEventListener("input", () => {
  if (!activeRuntimeSessionId) return;
  runtimeChartReady = false;
  runtimeDayReady = false;
  disconnectRuntimeConversation("출생 입력이 바뀌었습니다. 원국을 다시 계산한 뒤 새 대화에 연결하세요.");
});
byId("runtime-target-date").addEventListener("change", () => {
  runtimeDayReady = false;
  disconnectRuntimeConversation("선택 날짜가 바뀌었습니다. 같은 원국으로 새 연결 대화를 시작하세요.");
});
byId("runtime-today-button").addEventListener("click", () => {
  const input = byId("runtime-target-date");
  if (!runtimeCanaryStatus?.single_day_today_kst) return;
  input.value = runtimeCanaryStatus.single_day_today_kst;
  input.dispatchEvent(new Event("change", { bubbles: true }));
});

byId("runtime-connect-button").addEventListener("click", async () => {
  if (!runtimeChartReady || !activeRuntimeSessionId) return;
  const button = byId("runtime-connect-button");
  const targetDate = byId("runtime-target-date");
  if (!targetDate.reportValidity()) return;
  const selectedDate = targetDate.value;
  button.disabled = true;
  button.textContent = "날짜 계산·연결 중…";
  try {
    const result = await api(`/api/runtime/sessions/${activeRuntimeSessionId}/events`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        expected_revision: activeRuntimeRevision,
        event: {
          type: "request_period",
          request: {
            period_type: "day",
            start_date: selectedDate,
            end_date: selectedDate,
            timezone: "Asia/Seoul",
          },
        },
      }),
    });
    renderRuntimeFacts(result);
    if (!runtimeDayReady) throw new Error(result.decision?.message || "단일 일진 계산이 차단됐습니다.");
    activeSessionId = null;
    loadedSessionUpdatedAt = null;
    startingNewSession = true;
    selectedSessionRuntimeBound = true;
    connectedRuntimeSessionId = activeRuntimeSessionId;
    connectedManualSessionId = null;
    connectedRuntimeDate = selectedDate;
    byId("session-select").value = "";
    byId("manual-prompt").value = "";
    selectedPromptProfile = promptProfileCatalog?.bound_profile || "bound_chart_v1";
    selectedEngineSelection = inferenceEngineCatalog?.default_selection || "ki20_final";
    renderPromptProfile(selectedPromptProfile, true);
    renderEngineSelection(selectedEngineSelection, false);
    renderConversation(null);
    updateRuntimeConversationBinding();
    byId("runtime-status-copy").textContent = `${selectedDate} 단일 일진을 승인 원국과 새 대화에 연결했습니다.`;
    byId("manual-prompt").placeholder = `예: ${selectedDate}의 흐름을 원국과 함께 설명해줘`;
    byId("manual-prompt").focus();
    byId("manual-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    runtimeDayReady = false;
    disconnectRuntimeConversation(`날짜 계산·연결 실패: ${error.message}`);
    byId("runtime-status-copy").textContent = `날짜 계산·연결 실패: ${error.message}`;
  } finally {
    updateRuntimeConversationBinding();
  }
});

byId("runtime-chart-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!runtimeCanaryStatus?.enabled) return;
  const button = byId("runtime-chart-button");
  const precision = byId("runtime-time-precision").value;
  const calendar = byId("runtime-calendar").value;
  const events = [
    { type: "opt_in", accepted: byId("runtime-consent").checked },
    { type: "set_slot", field: "calendar", value: calendar },
    { type: "set_slot", field: "birth_date", value: byId("runtime-birth-date").value },
  ];
  if (calendar === "lunar") events.push({ type: "set_slot", field: "leap_month", value: byId("runtime-leap-month").value === "true" });
  events.push({ type: "set_slot", field: "birthplace", value: { country_code: "KR", city: byId("runtime-city").value.trim(), timezone: "Asia/Seoul" } });
  if (precision === "exact") events.push({ type: "set_slot", field: "birth_time", value: byId("runtime-birth-time").value });
  else if (precision === "range") events.push({ type: "set_slot", field: "time_range", value: { start: byId("runtime-range-start").value, end: byId("runtime-range-end").value } });
  else events.push({ type: "set_time_unknown" });
  events.push({ type: "request_chart" });
  button.disabled = true;
  button.textContent = "원국 계산 중…";
  runtimeChartReady = false;
  runtimeDayReady = false;
  disconnectRuntimeConversation("원국을 다시 계산하고 있습니다. 계산 완료 후 새 대화에 연결하세요.");
  byId("runtime-facts-panel").classList.add("hidden");
  byId("runtime-status-copy").textContent = "암호화 세션에서 승인 runtime을 실행하고 있습니다.";
  try {
    if (activeRuntimeSessionId) {
      await api(`/api/runtime/sessions/${activeRuntimeSessionId}`, { method: "DELETE" }).catch(() => null);
    }
    const created = await api("/api/runtime/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    activeRuntimeSessionId = created.session_id;
    activeRuntimeRevision = created.state_revision;
    let result = null;
    for (const runtimeEvent of events) {
      result = await api(`/api/runtime/sessions/${activeRuntimeSessionId}/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ expected_revision: activeRuntimeRevision, event: runtimeEvent }),
      });
      activeRuntimeRevision = result.state_revision;
    }
    renderRuntimeFacts(result);
    byId("runtime-status-copy").textContent = result.status === "ready"
      ? "승인 원국 사실을 계산했습니다. 날짜를 고른 뒤 ‘이 원국·날짜로 새 대화 시작’을 누르세요. exact 출생시각 원국만 날짜와 연결할 수 있습니다."
      : `원국 계산이 차단됐습니다: ${result.decision?.message || "입력 범위를 확인하세요."}`;
  } catch (error) {
    byId("runtime-status-copy").textContent = `원국 계산 실패: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = activeRuntimeSessionId ? "원국 다시 계산" : "원국 계산";
  }
});

byId("runtime-delete-button").addEventListener("click", async () => {
  if (!activeRuntimeSessionId) return;
  const button = byId("runtime-delete-button");
  button.disabled = true;
  button.textContent = "삭제 중…";
  try {
    await api(`/api/runtime/sessions/${activeRuntimeSessionId}`, { method: "DELETE" });
    activeRuntimeSessionId = null;
    activeRuntimeRevision = null;
    runtimeChartReady = false;
    runtimeDayReady = false;
    disconnectRuntimeConversation("출생 세션과 원국·날짜 대화 연결을 즉시 삭제했습니다.");
    byId("runtime-facts-panel").classList.add("hidden");
    byId("runtime-status-copy").textContent = "암호화 출생 세션을 즉시 삭제했습니다.";
  } catch (error) {
    byId("runtime-status-copy").textContent = `세션 삭제 실패: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "출생 세션 즉시 삭제";
  }
});

byId("manual-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (selectedSessionRuntimeBound && !runtimeConversationConnected()) {
    updateRuntimeConversationBinding("이 원국 대화의 연결이 만료됐습니다. 원국을 다시 계산하고 새 연결 대화를 시작하세요.");
    return;
  }
  const prompt = byId("manual-prompt").value.trim();
  if (!prompt) return;
  const button = byId("generate-button");
  button.disabled = true;
  button.textContent = "모델 로딩 중…";
  const selection = engineSelectionMetadata(selectedEngineSelection);
  byId("session-meta").textContent = selection?.mode === "paired" ? "K0와 KI20을 순서대로 불러오고 있습니다." : "선택한 모델을 별도 프로세스에서 불러오고 있습니다.";
  try {
    const result = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, session_id: activeSessionId, profile: selectedPromptProfile, engine_selection: selectedEngineSelection, runtime_session_id: connectedRuntimeSessionId }),
    });
    activeSessionId = result.session_id;
    if (result.runtime_binding_applied) {
      connectedManualSessionId = result.session_id;
      selectedSessionRuntimeBound = true;
    }
    startingNewSession = false;
    loadedSessionUpdatedAt = result.session.updated_at_utc;
    selectedPromptProfile = result.session.prompt_profile;
    selectedEngineSelection = result.session.engine_selection;
    renderPromptProfile(selectedPromptProfile, true);
    renderEngineSelection(selectedEngineSelection, true);
    byId("manual-prompt").value = "";
    renderConversation(result.session, result.contexts);
    updateRuntimeConversationBinding();
    await renderSessions(await api("/api/sessions"));
  } catch (error) {
    const prefix = error.code === "RUNTIME_GROUNDING_FAILED" ? "원국 grounding 실패" : "생성 실패";
    byId("session-meta").textContent = `${prefix}: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = engineSelectionMetadata(selectedEngineSelection)?.mode === "paired" ? "두 모델 비교" : "답변 생성";
  }
});

byId("run-probe-button").addEventListener("click", async () => {
  const button = byId("run-probe-button");
  button.disabled = true;
  button.textContent = "20건 생성·채점 중…";
  try {
    await api("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    await refresh();
  } catch (error) {
    renderAlerts([{ level: "critical", code: "fixed_probe", message: error.message }]);
  } finally {
    button.disabled = false;
    button.textContent = "고정 20건 검사 실행";
  }
});

updateRuntimeCalendarFields();
updateRuntimeTimeFields();
loadPromptExamples();
refresh();
setInterval(refresh, refreshMilliseconds);
