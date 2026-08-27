// team-review.js - 오프라인 팀 검수 의견·checkpoint·JSON·CSV 내보내기를 관리한다.

"use strict";

const reviewPackage = globalThis.TEAM_REVIEW_PACKAGE;

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
  activeId: null,
  source: "all",
  status: "all",
  feedback: new Map(),
  selectedDecision: null,
  dirty: false,
};

const feedbackControlPattern = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/;

const elements = {
  buildLabel: document.querySelector("#build-label"),
  packageLabel: document.querySelector("#package-label"),
  completedCount: document.querySelector("#completed-count"),
  remainingCount: document.querySelector("#remaining-count"),
  attentionCount: document.querySelector("#attention-count"),
  recordCount: document.querySelector("#record-count"),
  progress: document.querySelector("#progress"),
  message: document.querySelector("#message"),
  visibleCount: document.querySelector("#visible-count"),
  sourceFilter: document.querySelector("#source-filter"),
  statusFilter: document.querySelector("#status-filter"),
  queueList: document.querySelector("#queue-list"),
  emptyState: document.querySelector("#empty-state"),
  detailContent: document.querySelector("#detail-content"),
  detailOverline: document.querySelector("#detail-overline"),
  detailTitle: document.querySelector("#detail-title"),
  detailBadges: document.querySelector("#detail-badges"),
  correctionArea: document.querySelector("#correction-area"),
  recordArea: document.querySelector("#record-area"),
  projectionJson: document.querySelector("#projection-json"),
  feedbackPanel: document.querySelector("#feedback-panel"),
  feedbackState: document.querySelector("#feedback-state"),
  decisionOptions: document.querySelector("#decision-options"),
  reasonField: document.querySelector("#reason-field"),
  reasonCode: document.querySelector("#reason-code"),
  comment: document.querySelector("#comment"),
  saveFeedback: document.querySelector("#save-feedback"),
  nextPending: document.querySelector("#next-pending"),
  reviewerLabel: document.querySelector("#reviewer-label"),
  saveCheckpoint: document.querySelector("#save-checkpoint"),
  loadCheckpoint: document.querySelector("#load-checkpoint"),
  checkpointFile: document.querySelector("#checkpoint-file"),
  exportJson: document.querySelector("#export-json"),
  exportCsv: document.querySelector("#export-csv"),
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

function showMessage(message, success = false) {
  elements.message.textContent = message;
  elements.message.hidden = false;
  elements.message.classList.toggle("is-success", success);
}

function hideMessage() {
  elements.message.hidden = true;
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

function display(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function badge(text, className = "") {
  return node("span", { className: `badge ${className}`.trim(), text });
}

function paragraph(value) {
  return node("p", { text: display(value) });
}

function contentCard(title, children) {
  return node("section", { className: "content-card" }, [
    node("h4", { text: title }),
    ...children,
  ]);
}

function dialogueTurn(key) {
  const match = String(key).match(/(\d+)$/);
  return match ? Number(match[1]) : Number.MAX_SAFE_INTEGER;
}

function dialogueOrder(left, right) {
  const turnDifference = dialogueTurn(left) - dialogueTurn(right);
  if (turnDifference !== 0) return turnDifference;
  const leftSpeaker = left.startsWith("H") ? 0 : 1;
  const rightSpeaker = right.startsWith("H") ? 0 : 1;
  return leftSpeaker - rightSpeaker || left.localeCompare(right);
}

function facts(values) {
  const result = node("dl", { className: "facts" });
  values.filter((item) => item[1] !== undefined && item[1] !== null && item[1] !== "").forEach(([label, value]) => {
    result.append(node("div", { className: "fact" }, [
      node("dt", { text: label }),
      node("dd", { text: display(value) }),
    ]));
  });
  return result;
}

function renderPillars(value) {
  const pillars = parseMaybe(value);
  if (!pillars || typeof pillars !== "object") return paragraph(pillars);
  const result = node("div", { className: "pillars" });
  const labels = { year: "년주", month: "월주", day: "일주", hour: "시주" };
  Object.entries(labels).forEach(([key, label]) => {
    if (pillars[key] === undefined) return;
    const item = pillars[key];
    let primary = item;
    if (item && typeof item === "object") {
      primary = `${item.stem || item.heavenly_stem || ""} ${item.branch || item.earthly_branch || ""}`.trim() || display(item);
    }
    result.append(node("div", { className: "pillar" }, [
      node("small", { text: label }),
      node("strong", { text: primary }),
    ]));
  });
  return result;
}

function renderElements(value) {
  const parsed = parseMaybe(value);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return paragraph(parsed);
  const result = node("div", { className: "elements" });
  Object.entries(parsed).forEach(([key, item]) => {
    result.append(node("div", { className: "element" }, [
      node("small", { text: key }),
      node("strong", { text: display(item) }),
    ]));
  });
  return result;
}

function renderAihub(record) {
  const profile = record.profile || {};
  const persona = profile.persona || {};
  const emotion = profile.emotion || {};
  const content = (record.talk || {}).content || {};
  const dialogue = node("div", { className: "dialogue" });
  Object.keys(content).sort(dialogueOrder).forEach((key) => {
    const human = key.startsWith("H");
    dialogue.append(node("div", { className: `speech ${human ? "human" : "system"}` }, [
      node("small", { text: `${human ? "사용자" : "응답"} · ${key}` }),
      node("span", { text: content[key] }),
    ]));
  });
  return [
    contentCard("분류·감정 메타데이터", [facts([
      ["사용자 분류", persona.human],
      ["응답 분류", persona.computer],
      ["감정 유형", emotion.type],
      ["상황", emotion.situation],
    ])]),
    contentCard("공감 대화", [dialogue]),
  ];
}

function renderNemotron(record) {
  const context = record.persona_context || {};
  const chart = record.chart || {};
  const narrativeValue = parseMaybe((record.narrative || {}).saju_narrative);
  const narrativeNodes = [];
  if (narrativeValue && typeof narrativeValue === "object" && !Array.isArray(narrativeValue)) {
    Object.entries(narrativeValue).forEach(([key, value]) => {
      narrativeNodes.push(node("section", {}, [node("h4", { text: key }), paragraph(value)]));
    });
  } else {
    narrativeNodes.push(paragraph(narrativeValue));
  }
  return [
    contentCard("합성 인물 근거", [
      facts([
        ["성별", context.sex],
        ["나이", context.age],
        ["직업", context.occupation],
        ["지역", [context.district, context.province, context.country].filter(Boolean).join(", ")],
      ]),
      paragraph(context.persona),
      paragraph(context.cultural_background),
    ]),
    contentCard("명식·오행", [
      renderPillars(chart.saju_pillars),
      facts([
        ["일간", chart.saju_day_master],
        ["강한 오행", chart.saju_elements_dominant],
        ["부족 오행", chart.saju_elements_lacking],
      ]),
      renderElements(chart.saju_elements),
    ]),
    contentCard("사주 서술", narrativeNodes),
  ];
}

function renderBazi(record) {
  const value = record.facts || {};
  const rules = parseMaybe(record.retrieved_rules) || [];
  const list = node("ul", { className: "rule-list" });
  (Array.isArray(rules) ? rules : []).forEach((rule) => {
    list.append(node("li", {}, [
      node("strong", { text: rule.name || rule.id || "규칙" }),
      node("span", { text: rule.effect || rule.citation || display(rule) }),
      rule.citation ? node("small", { text: `근거: ${rule.citation}` }) : null,
    ]));
  });
  return [
    contentCard("질문·응답", [
      facts([["질문 유형", record.question_type], ["기준 연도", value.bazi_year]]),
      node("h4", { text: "질문" }),
      paragraph(record.user_question),
      node("h4", { text: "응답" }),
      paragraph(record.response),
    ]),
    contentCard("명식", [renderPillars(value.pillars), renderElements(value.element_counts)]),
    contentCard("검색 규칙", [list]),
  ];
}

function mappingTable(mapping) {
  if (!mapping || typeof mapping !== "object") return paragraph(mapping);
  const table = node("table", { className: "mapping-table" });
  const body = node("tbody");
  Object.entries(mapping).forEach(([key, value]) => {
    body.append(node("tr", {}, [
      node("th", { text: key, attrs: { scope: "row" } }),
      node("td", { text: display(value) }),
    ]));
  });
  table.append(body);
  return table;
}

function renderYeji(record) {
  const condition = record.condition || {};
  return [contentCard(`${record.name_ko || "규칙"} · ${record.name_cn || ""}`, [
    facts([["규칙 ID", record.id], ["유형", record.type], ["분류", record.category]]),
    node("h4", { text: "성립 조건" }),
    paragraph(condition.rule),
    mappingTable(condition.mapping),
    node("h4", { text: "의미" }),
    paragraph(record.meaning),
  ])];
}

function renderRecord(source, record) {
  if (source === "aihub_empathy") return renderAihub(record);
  if (source === "nemotron_saju") return renderNemotron(record);
  if (source === "bazi_sft") return renderBazi(record);
  if (source === "yeji_bazi_rules") return renderYeji(record);
  return [contentCard("레코드", [node("pre", { text: JSON.stringify(record, null, 2) })])];
}

function isAttention(feedback) {
  return feedback && !["accept", "exclude_candidate"].includes(feedback.suggested_decision);
}

function filteredItems() {
  return reviewPackage.items.filter((item) => {
    const feedback = state.feedback.get(item.review_id);
    const sourceMatch = state.source === "all" || state.source === item.source;
    let statusMatch = true;
    if (state.status === "pending") statusMatch = !feedback;
    if (state.status === "completed") statusMatch = Boolean(feedback);
    if (state.status === "attention") statusMatch = isAttention(feedback);
    return sourceMatch && statusMatch;
  });
}

function renderProgress() {
  const completed = state.feedback.size;
  const attention = [...state.feedback.values()].filter(isAttention).length;
  elements.completedCount.textContent = completed;
  elements.remainingCount.textContent = reviewPackage.unit_count - completed;
  elements.attentionCount.textContent = attention;
  elements.recordCount.textContent = reviewPackage.record_count;
  elements.progress.max = reviewPackage.unit_count;
  elements.progress.value = completed;
  elements.progress.textContent = `${completed} / ${reviewPackage.unit_count}`;
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
    const feedback = state.feedback.get(item.review_id);
    const attention = isAttention(feedback);
    const button = node("button", {
      className: ["queue-item", state.activeId === item.review_id ? "is-active" : "", feedback ? "is-complete" : ""].filter(Boolean).join(" "),
      attrs: { type: "button", role: "listitem" },
    }, [
      node("span", { className: "queue-index", text: String(item.index).padStart(3, "0") }),
      node("span", { className: "queue-main" }, [
        node("strong", { text: sourceLabels[item.source] || item.source }),
        node("small", { text: item.stratum }),
      ]),
      node("span", {
        className: `queue-dot ${attention ? "is-attention" : feedback ? "is-complete" : ""}`,
        attrs: { "aria-label": attention ? "확인 필요" : feedback ? "의견 저장됨" : "미검토" },
      }),
    ]);
    button.addEventListener("click", () => selectItem(item.review_id));
    elements.queueList.append(button);
  });
}

function renderCorrection(correction) {
  return node("section", { className: "correction-card" }, [
    node("h4", { text: `교정 overlay · ${correction.correction_id}` }),
    node("div", { className: "correction-change" }, [
      node("code", { text: correction.field_path.join(".") }),
      node("del", { text: display(correction.original) }),
      node("span", { text: "→" }),
      node("ins", { text: display(correction.replacement) }),
    ]),
    paragraph(correction.basis),
  ]);
}

function renderDecisionOptions() {
  clear(elements.decisionOptions);
  elements.decisionOptions.append(node("legend", { text: "제안 판정 선택" }));
  reviewPackage.decision_values.forEach((value, index) => {
    const button = node("button", {
      className: `decision-choice ${state.selectedDecision === value ? "is-selected" : ""}`,
      text: `${index + 1}. ${decisionLabels[value] || value}`,
      attrs: { type: "button", "aria-pressed": state.selectedDecision === value },
    });
    button.addEventListener("click", () => setDecision(value));
    elements.decisionOptions.append(button);
  });
}

function setDecision(value) {
  state.selectedDecision = value;
  renderDecisionOptions();
  const needsReason = value && value !== "accept";
  elements.reasonField.hidden = !needsReason;
  if (!needsReason) elements.reasonCode.value = "";
  elements.saveFeedback.disabled = !value;
}

function configureFeedback(item) {
  const feedback = state.feedback.get(item.review_id) || null;
  state.selectedDecision = feedback ? feedback.suggested_decision : null;
  clear(elements.reasonCode);
  elements.reasonCode.append(node("option", { text: "사유를 선택하세요", attrs: { value: "" } }));
  reviewPackage.reason_codes.forEach((reason) => {
    elements.reasonCode.append(node("option", { text: reasonLabels[reason] || reason, attrs: { value: reason } }));
  });
  elements.reasonCode.value = feedback && feedback.reason_code ? feedback.reason_code : "";
  elements.comment.value = feedback ? feedback.comment || "" : "";
  elements.feedbackState.textContent = feedback ? "저장됨" : "미저장";
  elements.reasonField.hidden = !state.selectedDecision || state.selectedDecision === "accept";
  elements.saveFeedback.disabled = !state.selectedDecision;
  renderDecisionOptions();
}

function renderItem(item) {
  elements.emptyState.hidden = true;
  elements.detailContent.hidden = false;
  elements.feedbackPanel.hidden = false;
  const queueLabel = item.queue === "required" ? "핵심 검수" : "참조 검수";
  elements.detailOverline.textContent = `${queueLabel} · ${item.stratum}`;
  elements.detailTitle.textContent = sourceLabels[item.source] || item.source;
  clear(elements.detailBadges);
  elements.detailBadges.append(badge(item.queue === "required" ? "핵심" : "참조", item.queue === "required" ? "is-core" : ""));
  elements.detailBadges.append(badge(item.unit_type === "pair" ? "2건 비교" : "단일 레코드"));
  item.flags.forEach((flag) => elements.detailBadges.append(badge(flag, "is-flag")));

  clear(elements.correctionArea);
  item.corrections.forEach((correction) => elements.correctionArea.append(renderCorrection(correction)));
  clear(elements.recordArea);
  const grid = node("div", { className: `record-grid ${item.records.length > 1 ? "is-pair" : ""}` });
  item.records.forEach((record, index) => {
    grid.append(node("article", { className: "record-card" }, [
      node("h3", { text: item.records.length > 1 ? `비교 레코드 ${index + 1}` : "검토 레코드" }),
      ...renderRecord(item.source, record),
    ]));
  });
  elements.recordArea.append(grid);
  clear(elements.projectionJson);
  elements.projectionJson.append(node("pre", { text: JSON.stringify({
    records: item.records,
    original_records: item.original_records || undefined,
    corrections: item.corrections,
  }, null, 2) }));
  configureFeedback(item);
}

function selectItem(reviewId) {
  const item = reviewPackage.items.find((value) => value.review_id === reviewId);
  if (!item) return;
  state.activeId = reviewId;
  renderQueue();
  renderItem(item);
}

function currentFeedback() {
  if (!state.activeId || !state.selectedDecision) throw new Error("제안 판정을 선택하세요.");
  const reason = state.selectedDecision === "accept" ? null : elements.reasonCode.value || null;
  const comment = elements.comment.value.trim();
  if (state.selectedDecision !== "accept" && !reason) throw new Error("비수락 판정에는 사유 코드가 필요합니다.");
  if (reason === "other" && !comment) throw new Error("other 사유에는 메모가 필요합니다.");
  if (comment.length > 2000 || feedbackControlPattern.test(comment)) throw new Error("팀원 메모에 허용되지 않는 문자가 있거나 2,000자를 넘습니다.");
  return {
    review_id: state.activeId,
    suggested_decision: state.selectedDecision,
    reason_code: reason,
    comment,
    reviewed_at: new Date().toISOString(),
  };
}

function saveCurrentFeedback() {
  hideMessage();
  try {
    const value = currentFeedback();
    state.feedback.set(value.review_id, value);
    state.dirty = true;
    elements.feedbackState.textContent = "저장됨";
    renderProgress();
    renderQueue();
    showMessage("현재 항목의 팀원 의견을 메모리에 저장했습니다.", true);
  } catch (error) {
    showMessage(error.message);
  }
}

function nextPending() {
  const items = filteredItems();
  const current = items.findIndex((item) => item.review_id === state.activeId);
  const ordered = [...items.slice(current + 1), ...items.slice(0, Math.max(current + 1, 0))];
  const next = ordered.find((item) => !state.feedback.has(item.review_id)) || ordered[0];
  if (next) selectItem(next.review_id);
}

function requireReviewerLabel() {
  const value = elements.reviewerLabel.value.trim();
  if (!value) throw new Error("검수자 표기를 입력하세요.");
  if (value.length > 80 || feedbackControlPattern.test(value)) throw new Error("검수자 표기가 올바르지 않습니다.");
  return value;
}

function feedbackDocument(exportKind) {
  const reviewer = requireReviewerLabel();
  const order = new Map(reviewPackage.items.map((item) => [item.review_id, item.index]));
  const suggestions = [...state.feedback.values()].sort((left, right) => order.get(left.review_id) - order.get(right.review_id));
  return {
    schema_version: "1.0.0",
    feedback_type: "advisory_team_review",
    export_kind: exportKind,
    package_id: reviewPackage.package_id,
    audit_version: reviewPackage.audit_version,
    build_id: reviewPackage.build_id,
    reviewer_label: reviewer,
    exported_at: new Date().toISOString(),
    completed_units: suggestions.length,
    total_units: reviewPackage.unit_count,
    suggestions,
  };
}

function downloadBlob(filename, type, content) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = node("a", { attrs: { href: url, download: filename } });
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

function exportJson(kind) {
  hideMessage();
  try {
    const value = feedbackDocument(kind);
    const filename = `team-review-${reviewPackage.build_id}-${kind}.json`;
    downloadBlob(filename, "application/json;charset=utf-8", `${JSON.stringify(value, null, 2)}\n`);
    state.dirty = false;
    showMessage(`${kind === "checkpoint" ? "진행" : "최종"} JSON을 내려받았습니다.`, true);
  } catch (error) {
    showMessage(error.message);
  }
}

function safeCsvCell(value) {
  let text = value === null || value === undefined ? "" : String(value);
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function exportCsv() {
  hideMessage();
  try {
    const value = feedbackDocument("final");
    const itemMap = new Map(reviewPackage.items.map((item) => [item.review_id, item]));
    const headers = ["package_id", "reviewer_label", "review_id", "index", "source", "stratum", "suggested_decision", "reason_code", "comment", "reviewed_at"];
    const rows = [headers.map(safeCsvCell).join(",")];
    value.suggestions.forEach((suggestion) => {
      const item = itemMap.get(suggestion.review_id);
      rows.push([
        value.package_id,
        value.reviewer_label,
        suggestion.review_id,
        item.index,
        item.source,
        item.stratum,
        suggestion.suggested_decision,
        suggestion.reason_code,
        suggestion.comment,
        suggestion.reviewed_at,
      ].map(safeCsvCell).join(","));
    });
    downloadBlob(`team-review-${reviewPackage.build_id}-final.csv`, "text/csv;charset=utf-8", `\ufeff${rows.join("\r\n")}\r\n`);
    state.dirty = false;
    showMessage("CSV를 내려받았습니다.", true);
  } catch (error) {
    showMessage(error.message);
  }
}

function validateImportedFeedback(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("checkpoint 최상위 값이 올바르지 않습니다.");
  const expectedKeys = ["schema_version", "feedback_type", "export_kind", "package_id", "audit_version", "build_id", "reviewer_label", "exported_at", "completed_units", "total_units", "suggestions"].sort();
  if (JSON.stringify(Object.keys(value).sort()) !== JSON.stringify(expectedKeys)) throw new Error("checkpoint 최상위 필드가 계약과 다릅니다.");
  if (value.schema_version !== "1.0.0" || value.feedback_type !== "advisory_team_review" || !["checkpoint", "final"].includes(value.export_kind)) throw new Error("지원하지 않는 checkpoint schema입니다.");
  if (value.package_id !== reviewPackage.package_id || value.audit_version !== reviewPackage.audit_version || value.build_id !== reviewPackage.build_id) throw new Error("다른 package 또는 build의 checkpoint입니다.");
  if (value.total_units !== reviewPackage.unit_count || typeof value.exported_at !== "string" || !Number.isFinite(Date.parse(value.exported_at))) throw new Error("checkpoint 수량 또는 내보낸 시각이 올바르지 않습니다.");
  if (typeof value.reviewer_label !== "string" || !value.reviewer_label.trim() || value.reviewer_label.length > 80 || feedbackControlPattern.test(value.reviewer_label)) throw new Error("checkpoint 검수자 표기가 올바르지 않습니다.");
  if (!Array.isArray(value.suggestions)) throw new Error("checkpoint suggestions가 배열이 아닙니다.");
  if (value.completed_units !== value.suggestions.length) throw new Error("checkpoint 완료 수량이 suggestions와 다릅니다.");
  const allowedIds = new Set(reviewPackage.items.map((item) => item.review_id));
  const result = new Map();
  value.suggestions.forEach((suggestion) => {
    if (!suggestion || typeof suggestion !== "object" || !allowedIds.has(suggestion.review_id)) throw new Error("checkpoint에 큐 밖 review_id가 있습니다.");
    const expectedSuggestionKeys = ["review_id", "suggested_decision", "reason_code", "comment", "reviewed_at"].sort();
    if (JSON.stringify(Object.keys(suggestion).sort()) !== JSON.stringify(expectedSuggestionKeys)) throw new Error("checkpoint suggestion 필드가 계약과 다릅니다.");
    if (result.has(suggestion.review_id)) throw new Error("checkpoint review_id가 중복됐습니다.");
    if (!reviewPackage.decision_values.includes(suggestion.suggested_decision)) throw new Error("checkpoint 판정값이 올바르지 않습니다.");
    if (suggestion.suggested_decision === "accept") {
      if (suggestion.reason_code !== null) throw new Error("accept checkpoint에 reason code가 있습니다.");
    } else if (!reviewPackage.reason_codes.includes(suggestion.reason_code)) {
      throw new Error("checkpoint reason code가 올바르지 않습니다.");
    }
    if (typeof suggestion.comment !== "string" || suggestion.comment.length > 2000 || feedbackControlPattern.test(suggestion.comment)) throw new Error("checkpoint 메모가 올바르지 않습니다.");
    if (suggestion.reason_code === "other" && !suggestion.comment.trim()) throw new Error("other checkpoint에 메모가 없습니다.");
    if (typeof suggestion.reviewed_at !== "string" || !Number.isFinite(Date.parse(suggestion.reviewed_at))) throw new Error("checkpoint 검수 시각이 올바르지 않습니다.");
    result.set(suggestion.review_id, {
      review_id: suggestion.review_id,
      suggested_decision: suggestion.suggested_decision,
      reason_code: suggestion.reason_code,
      comment: suggestion.comment,
      reviewed_at: suggestion.reviewed_at,
    });
  });
  return { reviewerLabel: value.reviewer_label.trim(), feedback: result };
}

async function importCheckpoint(file) {
  hideMessage();
  try {
    if (!file || file.size > 2 * 1024 * 1024) throw new Error("checkpoint 파일이 없거나 2MiB를 넘습니다.");
    const value = JSON.parse(await file.text());
    const imported = validateImportedFeedback(value);
    state.feedback = imported.feedback;
    state.dirty = false;
    elements.reviewerLabel.value = imported.reviewerLabel;
    renderProgress();
    renderQueue();
    if (state.activeId) selectItem(state.activeId);
    showMessage(`${state.feedback.size}건의 checkpoint를 불러왔습니다.`, true);
  } catch (error) {
    showMessage(error.message);
  } finally {
    elements.checkpointFile.value = "";
  }
}

function wireEvents() {
  elements.sourceFilter.addEventListener("change", () => {
    state.source = elements.sourceFilter.value;
    renderQueue();
  });
  elements.statusFilter.addEventListener("change", () => {
    state.status = elements.statusFilter.value;
    renderQueue();
  });
  elements.saveFeedback.addEventListener("click", saveCurrentFeedback);
  elements.nextPending.addEventListener("click", nextPending);
  elements.saveCheckpoint.addEventListener("click", () => exportJson("checkpoint"));
  elements.exportJson.addEventListener("click", () => exportJson("final"));
  elements.exportCsv.addEventListener("click", exportCsv);
  elements.loadCheckpoint.addEventListener("click", () => elements.checkpointFile.click());
  elements.checkpointFile.addEventListener("change", () => importCheckpoint(elements.checkpointFile.files[0]));
  window.addEventListener("beforeunload", (event) => {
    if (!state.dirty) return;
    event.preventDefault();
    event.returnValue = "";
  });
  document.addEventListener("keydown", (event) => {
    const editing = ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
    if (editing || !state.activeId) return;
    const index = Number(event.key) - 1;
    if (index >= 0 && index < reviewPackage.decision_values.length) {
      setDecision(reviewPackage.decision_values[index]);
      event.preventDefault();
    }
    if (event.key.toLowerCase() === "j") {
      nextPending();
      event.preventDefault();
    }
  });
}

function validatePackage() {
  if (!reviewPackage || typeof reviewPackage !== "object") throw new Error("review-data.js를 읽지 못했습니다.");
  if (reviewPackage.schema_version !== "1.0.0" || reviewPackage.feedback_type !== "advisory_team_review") throw new Error("지원하지 않는 team review package입니다.");
  if (reviewPackage.unit_count !== 300 || reviewPackage.record_count !== 340 || reviewPackage.items.length !== 300) throw new Error("핵심·참조 검수 수량 계약이 다릅니다.");
  const ids = new Set(reviewPackage.items.map((item) => item.review_id));
  if (ids.size !== reviewPackage.items.length) throw new Error("review_id가 중복됐습니다.");
}

function initialize() {
  try {
    validatePackage();
    wireEvents();
    elements.buildLabel.textContent = `${reviewPackage.audit_version} · ${reviewPackage.build_id}`;
    elements.packageLabel.textContent = reviewPackage.package_id;
    renderProgress();
    renderQueue();
    selectItem(reviewPackage.items[0].review_id);
  } catch (error) {
    elements.emptyState.querySelector("h2").textContent = "검수 패키지를 열 수 없습니다";
    elements.emptyState.querySelector("p").textContent = error.message;
    showMessage(error.message);
  }
}

initialize();
