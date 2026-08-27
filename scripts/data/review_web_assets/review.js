// review.js - 원문 렌더링과 append-only 판정 저장 흐름을 관리한다.

"use strict";

const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

const sourceLabels = {
  aihub_empathy: "AI Hub 공감대화",
  nemotron_saju: "Nemotron 사주",
  bazi_sft: "BaZi SFT",
  yeji_bazi_rules: "YEJI 규칙",
};

const decisionLabels = {
  accept: "수용",
  exclude_candidate: "제외 후보",
  rule_fix_required: "규칙 교정 필요",
  source_block: "원천 차단",
  uncertain: "판단 보류",
  skip: "건너뜀",
};

const reasonLabels = {
  safety_overclaim: "안전성 과장",
  unsafe_advice: "위험한 조언",
  pii: "개인정보",
  schema_error: "스키마 오류",
  rule_conflict: "규칙 충돌",
  mistranslation: "오역",
  factual_inconsistency: "사실 불일치",
  low_quality: "낮은 품질",
  other: "기타",
};

const state = {
  bootstrap: null,
  activeId: null,
  activeItem: null,
  queue: "required",
  source: "all",
  status: "all",
  decision: null,
  saving: false,
};

const elements = {
  connection: document.querySelector("#connection-badge"),
  buildLabel: document.querySelector("#build-label"),
  requiredCompleted: document.querySelector("#required-completed"),
  requiredRemaining: document.querySelector("#required-remaining"),
  requiredProgress: document.querySelector("#required-progress"),
  referenceCompleted: document.querySelector("#reference-completed"),
  historyCount: document.querySelector("#history-count"),
  visibleCount: document.querySelector("#visible-count"),
  sourceFilter: document.querySelector("#source-filter"),
  statusFilter: document.querySelector("#status-filter"),
  queueList: document.querySelector("#queue-list"),
  globalMessage: document.querySelector("#global-message"),
  emptyState: document.querySelector("#empty-state"),
  detailContent: document.querySelector("#detail-content"),
  detailOverline: document.querySelector("#detail-overline"),
  detailTitle: document.querySelector("#detail-title"),
  detailBadges: document.querySelector("#detail-badges"),
  findingBanner: document.querySelector("#finding-banner"),
  recordContent: document.querySelector("#record-content"),
  rawContent: document.querySelector("#raw-content"),
  decisionPanel: document.querySelector("#decision-panel"),
  revisionChip: document.querySelector("#revision-chip"),
  decisionOptions: document.querySelector("#decision-options"),
  reasonField: document.querySelector("#reason-field"),
  reasonCode: document.querySelector("#reason-code"),
  noteField: document.querySelector("#note-field"),
  privateNote: document.querySelector("#private-note"),
  saveDecision: document.querySelector("#save-decision"),
  nextPending: document.querySelector("#next-pending"),
  decisionHistory: document.querySelector("#decision-history"),
  confirmDialog: document.querySelector("#confirm-dialog"),
  confirmSummary: document.querySelector("#confirm-summary"),
  confirmSave: document.querySelector("#confirm-save"),
};

function node(tag, options = {}, children = []) {
  const value = document.createElement(tag);
  if (options.className) value.className = options.className;
  if (options.text !== undefined) value.textContent = String(options.text);
  if (options.attrs) {
    Object.entries(options.attrs).forEach(([name, attribute]) => {
      value.setAttribute(name, String(attribute));
    });
  }
  children.filter(Boolean).forEach((child) => value.append(child));
  return value;
}

function clear(target) {
  while (target.firstChild) target.firstChild.remove();
}

function showMessage(message, isError = true) {
  elements.globalMessage.textContent = message;
  elements.globalMessage.hidden = false;
  elements.globalMessage.classList.toggle("is-success", !isError);
}

function hideMessage() {
  elements.globalMessage.hidden = true;
}

async function api(path, options = {}) {
  const headers = {
    Accept: "application/json",
    "X-CSRF-Token": csrfToken,
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers, cache: "no-store" });
  const payload = await response.json().catch(() => ({ error: "JSON 응답을 읽지 못했습니다." }));
  if (!response.ok) throw new Error(payload.error || `요청 실패 (${response.status})`);
  return payload;
}

function parseMaybe(value) {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (!trimmed || !["{", "["].includes(trimmed[0])) return value;
  try {
    return JSON.parse(trimmed.replaceAll("'", '"'));
  } catch (_error) {
    return value;
  }
}

function stringifyCompact(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function badge(text, className = "") {
  return node("span", { className: `badge ${className}`.trim(), text });
}

function contentCard(title, children) {
  return node("section", { className: "content-card" }, [
    node("h4", { text: title }),
    ...children,
  ]);
}

function paragraph(value) {
  return node("p", { text: stringifyCompact(value) });
}

function facts(values) {
  const list = node("dl", { className: "fact-grid" });
  values.filter((item) => item[1] !== undefined && item[1] !== null && item[1] !== "").forEach(([label, value]) => {
    list.append(node("div", { className: "fact" }, [
      node("dt", { text: label }),
      node("dd", { text: stringifyCompact(value) }),
    ]));
  });
  return list;
}

function renderPillars(pillars) {
  const parsed = parseMaybe(pillars);
  if (!parsed || typeof parsed !== "object") return paragraph(parsed);
  const grid = node("div", { className: "pillar-grid" });
  ["year", "month", "day", "hour"].forEach((key) => {
    const value = parsed[key];
    if (value === undefined) return;
    let primary = value;
    if (value && typeof value === "object") {
      const stem = value.stem || value.heavenly_stem || "";
      const branch = value.branch || value.earthly_branch || "";
      primary = `${stem} ${branch}`.trim() || stringifyCompact(value);
    }
    grid.append(node("div", { className: "pillar" }, [
      node("small", { text: { year: "년주", month: "월주", day: "일주", hour: "시주" }[key] }),
      node("strong", { text: primary }),
    ]));
  });
  return grid;
}

function renderElements(values) {
  const parsed = parseMaybe(values);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return paragraph(parsed);
  const grid = node("div", { className: "element-grid" });
  Object.entries(parsed).forEach(([key, value]) => {
    grid.append(node("div", { className: "element" }, [
      node("small", { text: key }),
      node("strong", { text: stringifyCompact(value) }),
    ]));
  });
  return grid;
}

function renderAihub(record) {
  const profile = record.profile || {};
  const persona = profile.persona || {};
  const emotion = profile.emotion || {};
  const talk = record.talk || {};
  const content = talk.content || {};
  const dialogue = node("div", { className: "dialogue" });
  Object.keys(content).sort().forEach((key) => {
    const human = key.startsWith("H");
    dialogue.append(node("div", { className: `speech ${human ? "human" : "system"}` }, [
      node("small", { text: human ? `사용자 · ${key}` : `응답 · ${key}` }),
      node("span", { text: content[key] }),
    ]));
  });
  return [
    contentCard("프로필·감정 메타데이터", [facts([
      ["persona", persona["persona-id"]],
      ["human", persona.human],
      ["computer", persona.computer],
      ["감정 유형", emotion.type],
      ["상황", emotion.situation],
    ])]),
    contentCard("공감 대화", [dialogue]),
  ];
}

function renderNemotron(record) {
  const cards = [
    contentCard("합성 인물 요약", [
      facts([
        ["성별", record.sex],
        ["나이", record.age],
        ["직업", record.occupation],
        ["지역", [record.district, record.province, record.country].filter(Boolean).join(", ")],
      ]),
      paragraph(record.persona),
    ]),
    contentCard("사주 핵심값", [
      renderPillars(record.saju_pillars),
      facts([
        ["일간", record.saju_day_master],
        ["강한 오행", record.saju_elements_dominant],
        ["부족 오행", record.saju_elements_lacking],
      ]),
      renderElements(record.saju_elements),
    ]),
  ];
  const narrative = parseMaybe(record.saju_narrative);
  const narrativeNodes = [];
  if (narrative && typeof narrative === "object" && !Array.isArray(narrative)) {
    Object.entries(narrative).forEach(([key, value]) => {
      narrativeNodes.push(node("div", { className: "narrative-block" }, [
        node("h4", { text: key }),
        paragraph(value),
      ]));
    });
  } else {
    narrativeNodes.push(paragraph(narrative));
  }
  cards.push(contentCard("사주 서술", narrativeNodes));
  return cards;
}

function renderBazi(record) {
  const factsValue = parseMaybe(record.facts) || {};
  const birth = factsValue.birth_input || {};
  const rules = parseMaybe(record.retrieved_rules) || [];
  const ruleList = node("ul", { className: "rule-list" });
  (Array.isArray(rules) ? rules : []).forEach((rule) => {
    ruleList.append(node("li", {}, [
      node("strong", { text: rule.name || rule.id || "규칙" }),
      node("span", { text: rule.effect || rule.citation || stringifyCompact(rule) }),
      rule.citation ? node("small", { text: `근거: ${rule.citation}` }) : null,
    ]));
  });
  return [
    contentCard("질문·응답", [
      facts([
        ["질문 유형", record.question_type],
        ["장소", birth.place],
        ["생년월일", birth.date],
        ["시간", birth.time],
      ]),
      node("h4", { text: "사용자 질문" }),
      paragraph(record.user_question),
      node("h4", { text: "모델 응답" }),
      paragraph(record.response),
    ]),
    contentCard("명식", [renderPillars(factsValue.pillars), renderElements(factsValue.element_counts)]),
    contentCard("검색 규칙", [ruleList]),
  ];
}

function mappingTable(mapping) {
  if (!mapping || typeof mapping !== "object") return paragraph(mapping);
  const table = node("table", { className: "mapping-table" });
  const body = node("tbody");
  Object.entries(mapping).forEach(([key, value]) => {
    body.append(node("tr", {}, [
      node("th", { text: key, attrs: { scope: "row" } }),
      node("td", { text: stringifyCompact(value) }),
    ]));
  });
  table.append(body);
  return table;
}

function renderYeji(record) {
  const condition = record.condition || {};
  return [
    contentCard(`${record.name_ko || "규칙"} · ${record.name_cn || ""}`, [
      facts([
        ["규칙 ID", record.id],
        ["유형", record.type],
        ["분류", record.category],
      ]),
      node("h4", { text: "성립 조건" }),
      paragraph(condition.rule),
      mappingTable(condition.mapping),
      node("h4", { text: "의미" }),
      paragraph(record.meaning),
    ]),
  ];
}

function renderGeneric(record) {
  return [contentCard("레코드", [node("pre", { text: JSON.stringify(record, null, 2) })])];
}

function renderCorrection(correction) {
  return node("section", { className: "correction-card" }, [
    node("h4", { text: `검증 교정 overlay · ${correction.correction_id}` }),
    node("div", { className: "correction-change" }, [
      node("code", { text: correction.field_path.join(".") }),
      node("del", { text: stringifyCompact(correction.original) }),
      node("span", { text: "→" }),
      node("ins", { text: stringifyCompact(correction.replacement) }),
    ]),
    paragraph(correction.basis),
    node("small", { text: `해결 코드: ${(correction.resolves || []).join(", ")}` }),
  ]);
}

function renderRecord(source, record) {
  if (source === "aihub_empathy") return renderAihub(record);
  if (source === "nemotron_saju") return renderNemotron(record);
  if (source === "bazi_sft") return renderBazi(record);
  if (source === "yeji_bazi_rules") return renderYeji(record);
  return renderGeneric(record);
}

function attentionDecision(decision) {
  return decision && !["accept", "exclude_candidate"].includes(decision.decision);
}

function filteredItems() {
  if (!state.bootstrap) return [];
  return state.bootstrap.items.filter((item) => {
    const queueMatch = state.queue === "all" || item.queue === state.queue;
    const sourceMatch = state.source === "all" || item.source === state.source;
    const completed = Boolean(item.latest_decision);
    let statusMatch = true;
    if (state.status === "pending") statusMatch = !completed;
    if (state.status === "completed") statusMatch = completed;
    if (state.status === "attention") statusMatch = attentionDecision(item.latest_decision);
    return queueMatch && sourceMatch && statusMatch;
  });
}

function renderProgress(status) {
  elements.requiredCompleted.textContent = status.required_completed;
  elements.requiredRemaining.textContent = status.required_remaining;
  elements.referenceCompleted.textContent = `${status.reference_completed} / ${status.reference_total}`;
  elements.historyCount.textContent = status.decision_history_entries;
  elements.requiredProgress.max = status.required_total;
  elements.requiredProgress.value = status.required_completed;
  elements.requiredProgress.textContent = `${status.required_completed} / ${status.required_total}`;
}

function renderQueue() {
  clear(elements.queueList);
  const items = filteredItems();
  elements.visibleCount.textContent = `${items.length}건`;
  if (!items.length) {
    elements.queueList.append(node("p", { className: "empty-list", text: "현재 필터에 해당하는 항목이 없습니다." }));
    return;
  }
  items.forEach((item) => {
    const complete = Boolean(item.latest_decision);
    const attention = attentionDecision(item.latest_decision);
    const button = node("button", {
      className: [
        "queue-item",
        item.review_id === state.activeId ? "is-active" : "",
        complete ? "is-complete" : "",
      ].filter(Boolean).join(" "),
      attrs: { type: "button", role: "listitem", "data-review-id": item.review_id },
    }, [
      node("span", { className: "queue-index", text: String(item.index).padStart(3, "0") }),
      node("span", { className: "queue-main" }, [
        node("strong", { text: sourceLabels[item.source] || item.source }),
        node("small", { text: item.stratum }),
      ]),
      node("span", {
        className: `queue-state ${attention ? "is-attention" : complete ? "is-complete" : ""}`,
        attrs: { "aria-label": attention ? "확인 필요" : complete ? "검토 완료" : "미검토" },
      }),
    ]);
    button.addEventListener("click", () => selectItem(item.review_id));
    elements.queueList.append(button);
  });
}

function renderDecisionOptions(values) {
  clear(elements.decisionOptions);
  elements.decisionOptions.append(node("legend", { text: "판정 선택" }));
  values.forEach((value, index) => {
    const button = node("button", {
      className: `decision-choice ${state.decision === value ? "is-selected" : ""}`,
      text: `${index + 1}. ${decisionLabels[value] || value}`,
      attrs: { type: "button", "data-decision": value, "aria-pressed": state.decision === value },
    });
    button.addEventListener("click", () => setDecision(value));
    elements.decisionOptions.append(button);
  });
}

function setDecision(value) {
  state.decision = value;
  renderDecisionOptions(state.bootstrap.decision_values);
  const needsReason = value && value !== "accept";
  elements.reasonField.hidden = !needsReason;
  elements.noteField.hidden = !needsReason;
  if (!needsReason) {
    elements.reasonCode.value = "";
    elements.privateNote.value = "";
  }
  elements.saveDecision.disabled = !value || state.saving || state.activeItem.sealed;
}

function renderHistory(history) {
  clear(elements.decisionHistory);
  if (!history.length) {
    elements.decisionHistory.append(node("li", { className: "history-entry", text: "아직 저장된 판정이 없습니다." }));
    elements.revisionChip.textContent = "신규";
    return;
  }
  elements.revisionChip.textContent = `rev ${history.at(-1).revision}`;
  [...history].reverse().forEach((entry) => {
    const details = [
      entry.reason_code ? reasonLabels[entry.reason_code] || entry.reason_code : null,
      entry.private_note || null,
    ].filter(Boolean).join(" · ");
    elements.decisionHistory.append(node("li", { className: "history-entry" }, [
      node("strong", { text: `rev ${entry.revision} · ${decisionLabels[entry.decision] || entry.decision}` }),
      node("span", { text: entry.reviewed_at || "시간 정보 없음" }),
      details ? node("span", { text: details }) : null,
    ]));
  });
}

function configureDecisionPanel(item) {
  const latest = item.decision_history.at(-1) || null;
  state.decision = latest ? latest.decision : null;
  renderDecisionOptions(state.bootstrap.decision_values);
  clear(elements.reasonCode);
  elements.reasonCode.append(node("option", { text: "사유를 선택하세요", attrs: { value: "" } }));
  state.bootstrap.reason_codes.forEach((reason) => {
    elements.reasonCode.append(node("option", { text: reasonLabels[reason] || reason, attrs: { value: reason } }));
  });
  elements.reasonCode.value = latest && latest.reason_code ? latest.reason_code : "";
  elements.privateNote.value = latest && latest.private_note ? latest.private_note : "";
  const needsReason = state.decision && state.decision !== "accept";
  elements.reasonField.hidden = !needsReason;
  elements.noteField.hidden = !needsReason;
  elements.saveDecision.disabled = !state.decision || item.sealed;
  elements.saveDecision.textContent = latest ? "수정 판정 저장" : "판정 저장";
  renderHistory(item.decision_history);
}

function renderItem(item) {
  elements.emptyState.hidden = true;
  elements.detailContent.hidden = false;
  elements.decisionPanel.hidden = false;
  elements.detailOverline.textContent = `${item.queue === "required" ? "핵심" : "참조"} 검수 · ${item.stratum}`;
  elements.detailTitle.textContent = sourceLabels[item.source] || item.source;
  clear(elements.detailBadges);
  elements.detailBadges.append(badge(item.queue === "required" ? "필수" : "참조", item.queue === "required" ? "is-required" : "is-reference"));
  elements.detailBadges.append(badge(item.unit_type === "pair" ? "2건 비교" : "단일 레코드"));
  item.flags.forEach((flag) => elements.detailBadges.append(badge(flag, "is-flag")));
  const findingMessages = [];
  if (item.corrections.length) findingMessages.push(`이 항목에는 검증된 교정 overlay ${item.corrections.length}건이 적용되어 있습니다. 원본과 교정값을 함께 확인하세요.`);
  if (item.source === "yeji_bazi_rules" && state.bootstrap.resolved_finding_codes.length) {
    findingMessages.push(`감사에서 해결된 코드: ${state.bootstrap.resolved_finding_codes.join(", ")}`);
  }
  elements.findingBanner.hidden = !findingMessages.length;
  elements.findingBanner.textContent = findingMessages.join(" ");

  clear(elements.recordContent);
  item.corrections.forEach((correction) => elements.recordContent.append(renderCorrection(correction)));
  const recordGrid = node("div", { className: `record-grid ${item.records.length > 1 ? "is-pair" : ""}` });
  item.records.forEach((record, index) => {
    const card = node("article", { className: "record-card" }, [
      node("div", { className: "record-label" }, [
        node("strong", { text: item.records.length > 1 ? `비교 레코드 ${index + 1}` : "검토 레코드" }),
        node("span", { text: item.corrections.length ? "교정 overlay 기준" : "고정 원본" }),
      ]),
      ...renderRecord(item.source, record),
    ]);
    recordGrid.append(card);
  });
  elements.recordContent.append(recordGrid);

  clear(elements.rawContent);
  item.raw_records.forEach((record, index) => {
    elements.rawContent.append(node("pre", { text: JSON.stringify(record, null, 2), attrs: { "aria-label": `원본 JSON ${index + 1}` } }));
  });
  configureDecisionPanel(item);
}

async function selectItem(reviewId) {
  state.activeId = reviewId;
  renderQueue();
  elements.emptyState.hidden = false;
  elements.emptyState.querySelector("h2").textContent = "원문을 불러오는 중입니다";
  elements.detailContent.hidden = true;
  elements.decisionPanel.hidden = true;
  try {
    const item = await api(`/api/items/${reviewId}`);
    if (state.activeId !== reviewId) return;
    state.activeItem = item;
    renderItem(item);
  } catch (error) {
    showMessage(error.message);
    elements.emptyState.querySelector("h2").textContent = "원문을 불러오지 못했습니다";
  }
}

function updateBootstrapAfterSave(result) {
  const item = state.bootstrap.items.find((candidate) => candidate.review_id === state.activeId);
  if (item) {
    item.latest_decision = result.saved;
    item.history_count += 1;
  }
  state.bootstrap.status = result.status;
  renderProgress(result.status);
  renderQueue();
}

function decisionPayload() {
  const reason = state.decision === "accept" ? null : elements.reasonCode.value || null;
  const note = state.decision === "accept" ? null : elements.privateNote.value.trim() || null;
  if (state.decision !== "accept" && !reason) throw new Error("비수락 판정에는 사유 코드가 필요합니다.");
  if (reason === "other" && !note) throw new Error("other 사유에는 비공개 메모가 필요합니다.");
  return {
    review_id: state.activeId,
    decision: state.decision,
    reason_code: reason,
    private_note: note,
  };
}

function requestSave() {
  try {
    const payload = decisionPayload();
    const reason = payload.reason_code ? ` · ${reasonLabels[payload.reason_code] || payload.reason_code}` : "";
    elements.confirmSummary.textContent = `${decisionLabels[payload.decision] || payload.decision}${reason} 판정을 새 revision으로 기록합니다.`;
    elements.confirmDialog.showModal();
  } catch (error) {
    showMessage(error.message);
  }
}

async function saveDecision() {
  if (state.saving) return;
  state.saving = true;
  elements.saveDecision.disabled = true;
  hideMessage();
  try {
    const payload = decisionPayload();
    const result = await api("/api/decisions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    updateBootstrapAfterSave(result);
    showMessage(`rev ${result.saved.revision} 판정을 안전하게 저장했습니다.`, false);
    await selectItem(state.activeId);
  } catch (error) {
    showMessage(error.message);
  } finally {
    state.saving = false;
    elements.saveDecision.disabled = !state.decision || Boolean(state.activeItem && state.activeItem.sealed);
  }
}

function nextPending() {
  const all = filteredItems();
  if (!all.length) return;
  const current = all.findIndex((item) => item.review_id === state.activeId);
  const ordered = [...all.slice(current + 1), ...all.slice(0, Math.max(current + 1, 0))];
  const next = ordered.find((item) => !item.latest_decision) || ordered[0];
  if (next) selectItem(next.review_id);
}

function wireEvents() {
  document.querySelectorAll(".queue-tab").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll(".queue-tab").forEach((value) => value.classList.remove("is-active"));
      button.classList.add("is-active");
      state.queue = button.dataset.queue;
      renderQueue();
      const first = filteredItems()[0];
      if (first && !filteredItems().some((item) => item.review_id === state.activeId)) selectItem(first.review_id);
    });
  });
  elements.sourceFilter.addEventListener("change", () => {
    state.source = elements.sourceFilter.value;
    renderQueue();
  });
  elements.statusFilter.addEventListener("change", () => {
    state.status = elements.statusFilter.value;
    renderQueue();
  });
  elements.saveDecision.addEventListener("click", requestSave);
  elements.nextPending.addEventListener("click", nextPending);
  elements.confirmDialog.addEventListener("close", () => {
    if (elements.confirmDialog.returnValue === "confirm") saveDecision();
  });
  document.addEventListener("keydown", (event) => {
    const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (editing || !state.activeItem || state.saving) return;
    const index = Number(event.key) - 1;
    if (index >= 0 && index < state.bootstrap.decision_values.length) {
      setDecision(state.bootstrap.decision_values[index]);
      event.preventDefault();
    }
    if (event.key.toLowerCase() === "j") {
      nextPending();
      event.preventDefault();
    }
  });
}

async function initialize() {
  wireEvents();
  try {
    state.bootstrap = await api("/api/bootstrap");
    elements.connection.textContent = state.bootstrap.status.sealed ? "봉인됨 · 읽기 전용" : "로컬 연결됨";
    elements.connection.className = `status-badge ${state.bootstrap.status.sealed ? "is-sealed" : "is-online"}`;
    elements.buildLabel.textContent = `${state.bootstrap.identity.audit_version} · ${state.bootstrap.identity.build_id}`;
    renderProgress(state.bootstrap.status);
    renderQueue();
    const first = filteredItems().find((item) => !item.latest_decision) || filteredItems()[0];
    if (first) await selectItem(first.review_id);
  } catch (error) {
    elements.connection.textContent = "연결 실패";
    elements.connection.className = "status-badge is-loading";
    elements.emptyState.querySelector("h2").textContent = "로컬 검수 서버에 연결하지 못했습니다";
    elements.emptyState.querySelector("p").textContent = "이 파일을 직접 열지 말고 phase2_review_web.py로 실행하세요.";
    showMessage(error.message);
  }
}

initialize();
