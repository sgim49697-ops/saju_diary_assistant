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

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    cache: "no-store",
    headers: { "X-CSRF-Token": csrfToken, ...(options.headers || {}) },
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
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
  const cards = datasetCatalog.splits.map((split) => `<button type="button" class="split-card ${split.split_id === selectedDatasetSplit ? "selected" : ""}" data-split-id="${escapeHtml(split.split_id)}">
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
  byId("axis-distribution").innerHTML = split.axes.map((axis, index) => `<div class="axis-row">
    <div class="axis-copy"><strong>${escapeHtml(axis.label)}</strong><span>${number(axis.rows, 0)}행 · ${number(axis.percent, 2)}%${axis.restricted_local_only ? " · 제한" : ""}</span></div>
    <div class="axis-track"><div class="axis-fill axis-color-${index % 5}" style="width:${Math.max(1, axis.percent)}%"></div></div>
  </div>`).join("");
}

function renderDatasetControls() {
  const splitSelect = byId("dataset-split-select");
  splitSelect.innerHTML = datasetCatalog.splits.map((split) => `<option value="${escapeHtml(split.split_id)}">${escapeHtml(split.label)} · ${number(split.rows, 0)}행</option>`).join("");
  splitSelect.value = selectedDatasetSplit;
  const split = selectedSplit();
  const axisSelect = byId("dataset-axis-select");
  axisSelect.innerHTML = '<option value="all">전체 축 · 축별 1건</option>' + split.axes.map((axis) => `<option value="${escapeHtml(axis.axis)}">${escapeHtml(axis.label)} · 3건</option>`).join("");
  if (selectedDatasetAxis !== "all" && !split.axes.some((axis) => axis.axis === selectedDatasetAxis)) selectedDatasetAxis = "all";
  axisSelect.value = selectedDatasetAxis;
}

function sampleMessages(messages) {
  return (messages || []).map((message) => `<div class="sample-message ${escapeHtml(message.role)}">
    <span>${escapeHtml(({ system: "SYSTEM", user: "USER", assistant: "ASSISTANT" })[message.role] || message.role)}</span>
    <p>${escapeHtml(message.content)}</p>
  </div>`).join("");
}

function renderDatasetSamples(payload) {
  byId("dataset-restriction").classList.toggle("hidden", !payload.restricted_content_included);
  byId("dataset-samples").innerHTML = payload.items.map((item, index) => {
    const body = item.format === "messages"
      ? sampleMessages(item.messages)
      : `<div class="structured-sample"><div><span>INPUT</span><pre>${escapeHtml(JSON.stringify(item.input, null, 2))}</pre></div><div><span>EXPECTED</span><pre>${escapeHtml(JSON.stringify(item.expected, null, 2))}</pre></div></div>`;
    return `<article class="dataset-sample-card">
      <header><div><span class="sample-number">${index + 1}</span><strong>${escapeHtml(item.axis_label)}</strong></div><div class="sample-badges"><span>${escapeHtml(item.task || "sample")}</span>${item.restricted_local_only ? '<span class="restricted-badge">로컬 제한</span>' : ""}</div></header>
      ${body}
      <footer><code>${escapeHtml(item.sample_key)}</code>${item.reference_available === false ? " · reference 없음" : ""}</footer>
    </article>`;
  }).join("");
}

async function loadDatasetSamples() {
  const key = `${selectedDatasetSplit}/${selectedDatasetAxis}`;
  if (loadedDatasetKey === key) return;
  loadedDatasetKey = key;
  byId("dataset-samples").innerHTML = '<p class="empty-conversation">고정 hash와 샘플 연결을 확인하는 중입니다.</p>';
  byId("dataset-restriction").classList.add("hidden");
  try {
    const payload = await api(`/api/dataset-samples/${selectedDatasetSplit}/${selectedDatasetAxis}`);
    if (`${selectedDatasetSplit}/${selectedDatasetAxis}` !== key) return;
    renderDatasetSamples(payload);
  } catch (error) {
    if (`${selectedDatasetSplit}/${selectedDatasetAxis}` !== key) return;
    loadedDatasetKey = null;
    byId("dataset-samples").innerHTML = `<p class="empty-conversation">샘플 로드 실패: ${escapeHtml(error.message)}</p>`;
  }
}

function selectDatasetSplit(splitId) {
  if (!datasetCatalog?.splits.some((split) => split.split_id === splitId)) return;
  selectedDatasetSplit = splitId;
  selectedDatasetAxis = "all";
  loadedDatasetKey = null;
  renderSplitCards();
  renderAxisDistribution();
  renderDatasetControls();
  loadDatasetSamples();
}

function renderDatasetCatalog(payload) {
  datasetCatalog = payload;
  if (!payload.splits.some((split) => split.split_id === selectedDatasetSplit)) selectedDatasetSplit = payload.splits[0].split_id;
  renderSplitCards();
  renderAxisDistribution();
  renderDatasetControls();
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
  select.innerHTML = items.map((item) => `<option value="${escapeHtml(item.profile_id)}">${escapeHtml(item.label)}${item.diagnostic_only ? " · 진단" : " · 기본"}</option>`).join("");
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
    const requested = profileMetadata(selectedPromptProfile) ? selectedPromptProfile : payload.prompt_profiles.default_profile;
    const requestedEngine = engineSelectionMetadata(selectedEngineSelection) ? selectedEngineSelection : payload.inference_engines.default_selection;
    renderPromptProfile(requested, false);
    renderEngineSelection(requestedEngine, false);
    if (loadedSessionUpdatedAt !== null || startingNewSession || !payload.items.length) {
      loadedSessionUpdatedAt = null;
      renderConversation(null);
    }
    return;
  }
  const summary = payload.items.find((item) => item.session_id === activeSessionId);
  if (summary) {
    renderPromptProfile(summary.prompt_profile, true);
    renderEngineSelection(summary.engine_selection, true);
  }
  if (summary && summary.updated_at_utc !== loadedSessionUpdatedAt) {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderConversation(session);
  }
}

async function refresh() {
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
  activeSessionId = event.target.value || null;
  loadedSessionUpdatedAt = null;
  startingNewSession = activeSessionId === null;
  if (!activeSessionId) {
    selectedPromptProfile = promptProfileCatalog?.default_profile || "guided_diagnostic_v1";
    selectedEngineSelection = inferenceEngineCatalog?.default_selection || "ki20_final";
    renderPromptProfile(selectedPromptProfile, false);
    renderEngineSelection(selectedEngineSelection, false);
    renderConversation(null);
    return;
  }
  try {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderPromptProfile(session.prompt_profile, true);
    renderEngineSelection(session.engine_selection, true);
    renderConversation(session);
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

byId("dataset-split-select").addEventListener("change", (event) => {
  selectDatasetSplit(event.target.value);
});

byId("dataset-axis-select").addEventListener("change", (event) => {
  selectedDatasetAxis = event.target.value;
  loadedDatasetKey = null;
  loadDatasetSamples();
});

byId("manual-form").addEventListener("submit", async (event) => {
  event.preventDefault();
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
      body: JSON.stringify({ prompt, session_id: activeSessionId, profile: selectedPromptProfile, engine_selection: selectedEngineSelection }),
    });
    activeSessionId = result.session_id;
    startingNewSession = false;
    loadedSessionUpdatedAt = result.session.updated_at_utc;
    selectedPromptProfile = result.session.prompt_profile;
    selectedEngineSelection = result.session.engine_selection;
    renderPromptProfile(selectedPromptProfile, true);
    renderEngineSelection(selectedEngineSelection, true);
    byId("manual-prompt").value = "";
    renderConversation(result.session, result.contexts);
    await renderSessions(await api("/api/sessions"));
  } catch (error) {
    byId("session-meta").textContent = `생성 실패: ${error.message}`;
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

refresh();
setInterval(refresh, refreshMilliseconds);
