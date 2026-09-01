// candidate.js - 구조화 event만 전송하고 후보 결과의 공개 allowlist만 표시한다.

"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;
let sessionId = null;

const byId = (id) => document.getElementById(id);

async function api(path, options = {}) {
  const response = await fetch(path, {
    cache: "no-store",
    credentials: "same-origin",
    ...options,
    headers: {
      "X-CSRF-Token": csrfToken,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

async function createSession() {
  const payload = await api("/api/runtime/historical-candidate/sessions", {
    method: "POST",
    body: "{}",
  });
  sessionId = payload.session_id;
  byId("session-state").textContent = "메모리 세션 활성";
  return payload;
}

async function sendEvent(event) {
  if (!sessionId) await createSession();
  return api(`/api/runtime/historical-candidate/sessions/${sessionId}/events`, {
    method: "POST",
    body: JSON.stringify({ event }),
  });
}

function setBusy(busy, copy = "") {
  byId("calculate").disabled = busy;
  byId("period-check").disabled = busy;
  byId("form-status").textContent = copy;
}

function renderPillars(pillars) {
  const root = byId("pillars");
  root.replaceChildren();
  const labels = { year: "년주", month: "월주", day: "일주", hour: "시주" };
  Object.entries(labels).forEach(([key, label]) => {
    const article = document.createElement("article");
    article.className = "pillar";
    const title = document.createElement("span");
    title.textContent = label;
    const value = document.createElement("strong");
    value.textContent = pillars?.[key]?.ganzhi || "미정";
    article.append(title, value);
    root.append(article);
  });
}

function renderResponse(payload) {
  byId("empty-result").classList.add("hidden");
  byId("candidate-result").classList.add("hidden");
  byId("blocked-result").classList.add("hidden");
  if (payload.status === "candidate_ready" && payload.result) {
    const facts = payload.result.hard_facts;
    const evidence = facts.solar_term_evidence;
    renderPillars(facts.pillars);
    byId("day-master").textContent = `${facts.day_master?.stem || "—"} · ${facts.day_master?.element || "—"}`;
    byId("evidence-authority").textContent = evidence?.overall_authority || "—";
    byId("snapshot-cutoff").textContent = evidence?.official_snapshot_collected_at || "—";
    byId("facts-json").textContent = JSON.stringify(facts, null, 2);
    byId("authority").textContent = payload.result.fact_authority;
    byId("authority").classList.remove("muted");
    byId("candidate-result").classList.remove("hidden");
    byId("form-status").textContent = "과거 공식 근거 Gate를 통과했습니다.";
    return;
  }
  byId("authority").textContent = "결과 없음";
  byId("authority").classList.add("muted");
  if (payload.status === "blocked") {
    byId("blocked-code").textContent = payload.decision.reason_code || "CANDIDATE_BLOCKED";
    byId("blocked-message").textContent = payload.decision.message || "후보 범위 밖입니다.";
    byId("blocked-result").classList.remove("hidden");
  } else {
    byId("empty-result").classList.remove("hidden");
  }
}

function syncFields() {
  const lunar = byId("calendar").value === "lunar";
  byId("leap-field").classList.toggle("hidden", !lunar);
  const precision = byId("time-precision").value;
  byId("exact-time-field").classList.toggle("hidden", precision !== "exact");
  byId("range-time-field").classList.toggle("hidden", precision !== "range");
}

async function submitCandidate(event) {
  event.preventDefault();
  if (!byId("opt-in").checked) return;
  setBusy(true, "구조화 입력과 후보 권한을 확인 중입니다…");
  try {
    if (!sessionId) await createSession();
    await sendEvent({ type: "reset" });
    await sendEvent({ type: "opt_in", accepted: true });
    await sendEvent({ type: "set_slot", field: "birth_date", value: byId("birth-date").value });
    const calendar = byId("calendar").value;
    await sendEvent({ type: "set_slot", field: "calendar", value: calendar });
    if (calendar === "lunar") {
      await sendEvent({ type: "set_slot", field: "leap_month", value: byId("leap-month").value === "true" });
    }
    const precision = byId("time-precision").value;
    if (precision === "unknown") {
      await sendEvent({ type: "set_time_unknown" });
    } else {
      await sendEvent({ type: "set_slot", field: "time_precision", value: precision });
      const field = precision === "exact" ? "birth_time" : "time_range";
      const value = precision === "exact"
        ? byId("birth-time").value
        : { start: byId("range-start").value, end: byId("range-end").value };
      await sendEvent({ type: "set_slot", field, value });
    }
    const result = await sendEvent({
      type: "set_slot",
      field: "birthplace",
      value: {
        country_code: "KR",
        city: byId("city").value,
        timezone: "Asia/Seoul",
        longitude: null,
        latitude: null,
      },
    });
    renderResponse(result);
  } catch (error) {
    sessionId = null;
    byId("session-state").textContent = "세션 재생성 필요";
    byId("form-status").textContent = error.message;
  } finally {
    setBusy(false, byId("form-status").textContent);
  }
}

async function checkPeriodBlock() {
  setBusy(true, "기간 범위 차단을 확인 중입니다…");
  try {
    const result = await sendEvent({ type: "request_period" });
    renderResponse(result);
  } catch (error) {
    byId("form-status").textContent = error.message;
  } finally {
    setBusy(false, byId("form-status").textContent);
  }
}

async function loadStatus() {
  try {
    const status = await api("/api/runtime/historical-candidate/status");
    byId("runtime-status").textContent = "후보 runtime 사용 가능";
    byId("runtime-detail").textContent = `세션 ${status.active_sessions}/${status.session_limit} · release 미승인`;
    byId("status-dot").classList.add("ready");
  } catch (error) {
    byId("runtime-status").textContent = "후보 runtime 확인 실패";
    byId("runtime-detail").textContent = error.message;
  }
}

byId("calendar").addEventListener("change", syncFields);
byId("time-precision").addEventListener("change", syncFields);
byId("candidate-form").addEventListener("submit", submitCandidate);
byId("period-check").addEventListener("click", checkPeriodBlock);
syncFields();
loadStatus();
