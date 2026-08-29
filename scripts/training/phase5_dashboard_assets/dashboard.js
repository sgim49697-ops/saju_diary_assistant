// dashboard.js - KI20 상태 API를 주기적으로 읽어 차트와 모델 검사 화면을 갱신한다.

"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
const refreshMilliseconds = 10_000;
let activeTab = "training";
let activeSessionId = null;
let loadedSessionUpdatedAt = null;
let startingNewSession = false;

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
  byId("comparison-list").innerHTML = payload.rows.map((row, index) => `<details class="comparison-item">
    <summary>${index + 1}. ${escapeHtml(row.category)} · ${escapeHtml(row.eval_id)}</summary>
    <div class="comparison-body">
      <div class="comparison-prompt"><span class="label">PROMPT</span><p>${escapeHtml(promptText(row.prompt_messages))}</p></div>
      <div class="comparison-column"><span class="label">KI10</span><p>${escapeHtml(row.ki10_output)}</p></div>
      <div class="comparison-column"><span class="label">KI20</span><p>${escapeHtml(row.ki20_output)}</p></div>
    </div>
  </details>`).join("");
}

function renderConversation(session, context = null) {
  const target = byId("manual-conversation");
  if (!session) {
    target.innerHTML = '<p class="empty-conversation">새 세션입니다. 첫 질문을 입력하세요.</p>';
    byId("session-meta").textContent = "새 세션에서 첫 질문을 입력하세요.";
    return;
  }
  target.innerHTML = session.messages.map((message) => `<article class="message ${escapeHtml(message.role)}">
    <div class="message-head"><strong>${message.role === "user" ? "사용자" : "KI20"}</strong><span>${escapeHtml(localTime(message.created_at_utc))}</span></div>
    <p>${escapeHtml(message.content)}</p>
  </article>`).join("");
  const omitted = context?.omitted_turns ? ` · 오래된 ${context.omitted_turns} turn 입력 제외` : "";
  byId("session-meta").textContent = `${session.turn_count} turn · 최근 저장 ${localTime(session.updated_at_utc)}${omitted}`;
  target.scrollTop = target.scrollHeight;
}

async function renderSessions(payload) {
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
    `<option value="${escapeHtml(item.session_id)}">${escapeHtml(item.title)} · ${item.turn_count} turn</option>`
  ).join("");
  select.value = activeSessionId || "";
  if (!activeSessionId) {
    if (loadedSessionUpdatedAt !== null || startingNewSession || !payload.items.length) {
      loadedSessionUpdatedAt = null;
      renderConversation(null);
    }
    return;
  }
  const summary = payload.items.find((item) => item.session_id === activeSessionId);
  if (summary && summary.updated_at_utc !== loadedSessionUpdatedAt) {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderConversation(session);
  }
}

async function refresh() {
  try {
    const [status, metrics, checkpoints, checks, sessions] = await Promise.all([
      api("/api/status"), api("/api/metrics"), api("/api/checkpoints"), api("/api/model-checks"), api("/api/sessions"),
    ]);
    renderStatus(status);
    renderMetrics(metrics);
    renderCheckpoints(checkpoints);
    renderModelChecks(checks);
    await renderSessions(sessions);
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
  renderConversation(null);
  byId("manual-prompt").focus();
});

byId("session-select").addEventListener("change", async (event) => {
  activeSessionId = event.target.value || null;
  loadedSessionUpdatedAt = null;
  startingNewSession = activeSessionId === null;
  if (!activeSessionId) {
    renderConversation(null);
    return;
  }
  try {
    const session = await api(`/api/sessions/${activeSessionId}`);
    loadedSessionUpdatedAt = session.updated_at_utc;
    renderConversation(session);
  } catch (error) {
    byId("session-meta").textContent = `세션 로드 실패: ${error.message}`;
  }
});

byId("manual-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const prompt = byId("manual-prompt").value.trim();
  if (!prompt) return;
  const button = byId("generate-button");
  button.disabled = true;
  button.textContent = "모델 로딩 중…";
  byId("session-meta").textContent = "final checkpoint를 별도 프로세스에서 불러오고 있습니다.";
  try {
    const result = await api("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt, session_id: activeSessionId }),
    });
    activeSessionId = result.session_id;
    startingNewSession = false;
    loadedSessionUpdatedAt = result.session.updated_at_utc;
    byId("manual-prompt").value = "";
    renderConversation(result.session, result.context);
    await renderSessions(await api("/api/sessions"));
  } catch (error) {
    byId("session-meta").textContent = `생성 실패: ${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "답변 생성";
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
