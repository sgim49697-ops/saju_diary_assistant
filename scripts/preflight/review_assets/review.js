// review.js - Phase 4 K0 오프라인 검수 상태와 JSON 내보내기를 관리한다.

(function () {
  "use strict";

  const payload = window.PHASE4_REVIEW_DATA;
  if (!payload || !Array.isArray(payload.items) || payload.items.length !== 700) {
    document.body.textContent = "검수 데이터가 없거나 패키지 수량이 올바르지 않습니다.";
    return;
  }

  const storageKey = `phase4-review:${payload.package_id}`;
  const elements = {
    packageMeta: document.getElementById("package-meta"),
    reviewedCount: document.getElementById("reviewed-count"),
    progress: document.getElementById("review-progress"),
    passCount: document.getElementById("pass-count"),
    reviseCount: document.getElementById("revise-count"),
    blockCount: document.getElementById("block-count"),
    filter: document.getElementById("filter"),
    categoryFilter: document.getElementById("category-filter"),
    list: document.getElementById("item-list"),
    kicker: document.getElementById("item-kicker"),
    title: document.getElementById("item-title"),
    badge: document.getElementById("auto-badge"),
    cases: document.getElementById("case-container"),
    note: document.getElementById("review-note"),
    previous: document.getElementById("previous"),
    saveNext: document.getElementById("save-next"),
    exportCheckpoint: document.getElementById("export-checkpoint"),
    exportFinal: document.getElementById("export-final")
  };

  let state = loadState();
  let activeIndex = 0;

  function loadState() {
    try {
      const value = JSON.parse(localStorage.getItem(storageKey) || "{}");
      if (value.package_id !== payload.package_id || typeof value.decisions !== "object") {
        return { package_id: payload.package_id, decisions: {} };
      }
      return value;
    } catch (_) {
      return { package_id: payload.package_id, decisions: {} };
    }
  }

  function persist() {
    localStorage.setItem(storageKey, JSON.stringify(state));
  }

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function filteredIndexes() {
    const verdictFilter = elements.filter.value;
    const category = elements.categoryFilter.value;
    return payload.items.map((_, index) => index).filter((index) => {
      const item = payload.items[index];
      const decision = state.decisions[item.review_id];
      const verdict = decision ? decision.verdict : "pending";
      return (verdictFilter === "all" || verdictFilter === verdict)
        && (category === "all" || item.category === category);
    });
  }

  function renderSummary() {
    const counts = { pass: 0, revise: 0, block: 0 };
    Object.values(state.decisions).forEach((decision) => {
      if (counts[decision.verdict] !== undefined) counts[decision.verdict] += 1;
    });
    const reviewed = counts.pass + counts.revise + counts.block;
    elements.reviewedCount.textContent = `${reviewed} / ${payload.items.length}`;
    elements.progress.value = reviewed;
    elements.passCount.textContent = counts.pass;
    elements.reviseCount.textContent = counts.revise;
    elements.blockCount.textContent = counts.block;
  }

  function renderList() {
    elements.list.replaceChildren();
    filteredIndexes().forEach((index) => {
      const item = payload.items[index];
      const decision = state.decisions[item.review_id];
      const button = element("button", `item-button${index === activeIndex ? " active" : ""}`);
      button.type = "button";
      button.append(`${item.review_id} · ${item.category}`);
      const stateLabel = element("span", `state ${decision ? decision.verdict : ""}`, decision ? ({ pass: "통과", revise: "수정", block: "차단" })[decision.verdict] : "대기");
      button.append(stateLabel);
      button.addEventListener("click", () => {
        saveActive(false);
        activeIndex = index;
        render();
      });
      elements.list.append(button);
    });
  }

  function renderCase(caseValue, index) {
    const card = element("section", "case-card");
    card.append(element("h3", "", `Case ${index + 1}`));
    caseValue.prompt_messages.forEach((message) => {
      const box = element("div", `message ${message.role}`);
      box.append(element("span", "label", message.role));
      box.append(document.createTextNode(message.content));
      card.append(box);
    });
    if (caseValue.reference_assistant !== null) {
      const reference = element("div", "reference");
      reference.append(element("span", "label", "reference"));
      reference.append(document.createTextNode(caseValue.reference_assistant));
      card.append(reference);
    }
    const output = element("div", "output");
    output.append(element("span", "label", "K0 model output"));
    output.append(document.createTextNode(caseValue.model_output));
    card.append(output);
    const contract = caseValue.metrics.automated_contract_pass;
    const metricText = `자동 계약: ${contract === null ? "진단 전용" : contract ? "통과" : "실패"} · 생성 ${caseValue.generated_tokens} tokens · EOS ${caseValue.finished_with_eos ? "예" : "아니오"}`;
    card.append(element("p", "metrics", metricText));
    return card;
  }

  function renderItem() {
    const item = payload.items[activeIndex];
    const decision = state.decisions[item.review_id] || {};
    elements.kicker.textContent = `${item.split} · ${item.hardness} · ${item.source_axis || "공개 합성"}`;
    elements.title.textContent = `${item.review_id} · ${item.category}`;
    elements.cases.replaceChildren(...item.cases.map(renderCase));
    const contractValues = item.cases.map((value) => value.metrics.automated_contract_pass).filter((value) => value !== null);
    elements.badge.className = "badge neutral";
    elements.badge.textContent = "자동 진단 전용";
    if (contractValues.length) {
      const passed = contractValues.every(Boolean);
      elements.badge.className = `badge ${passed ? "pass" : "fail"}`;
      elements.badge.textContent = passed ? "자동 계약 통과" : "자동 계약 확인 필요";
    }
    document.querySelectorAll('input[name="verdict"]').forEach((radio) => {
      radio.checked = radio.value === decision.verdict;
    });
    elements.note.value = decision.note || "";
  }

  function saveActive(requireVerdict) {
    const item = payload.items[activeIndex];
    const checked = document.querySelector('input[name="verdict"]:checked');
    if (!checked) {
      if (requireVerdict) alert("사람 판정을 선택해 주세요.");
      return !requireVerdict;
    }
    state.decisions[item.review_id] = {
      verdict: checked.value,
      note: elements.note.value.trim()
    };
    persist();
    return true;
  }

  function nextVisible(delta) {
    const indexes = filteredIndexes();
    const position = indexes.indexOf(activeIndex);
    if (!indexes.length) return;
    activeIndex = indexes[Math.min(indexes.length - 1, Math.max(0, position + delta))];
    render();
  }

  function exportJson(finalExport) {
    saveActive(false);
    const decisions = payload.items.map((item) => ({
      review_id: item.review_id,
      verdict: state.decisions[item.review_id]?.verdict || null,
      note: state.decisions[item.review_id]?.note || ""
    }));
    const reviewed = decisions.filter((value) => value.verdict !== null).length;
    if (finalExport && reviewed !== payload.items.length) {
      alert(`최종 내보내기 전에 ${payload.items.length - reviewed}개 항목을 더 검수해 주세요.`);
      return;
    }
    const output = {
      schema_version: "1.0.0",
      feedback_type: finalExport ? "final" : "checkpoint",
      package_id: payload.package_id,
      build_id: payload.build_id,
      expected_item_count: payload.items.length,
      reviewed_item_count: reviewed,
      human_domain_review_performed: finalExport,
      decisions
    };
    const blob = new Blob([JSON.stringify(output, null, 2) + "\n"], { type: "application/json" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `${payload.package_id}-${finalExport ? "final" : "checkpoint"}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
  }

  function render() {
    renderSummary();
    renderList();
    renderItem();
  }

  const categories = [...new Set(payload.items.map((item) => item.category))].sort();
  categories.forEach((category) => {
    const option = element("option", "", category);
    option.value = category;
    elements.categoryFilter.append(option);
  });
  elements.packageMeta.textContent = `${payload.package_id} · ${payload.items.length}항목 / ${payload.generation_cases}case · 모델 ${payload.model_revision.slice(0, 12)}`;
  elements.filter.addEventListener("change", renderList);
  elements.categoryFilter.addEventListener("change", renderList);
  elements.previous.addEventListener("click", () => { saveActive(false); nextVisible(-1); });
  elements.saveNext.addEventListener("click", () => { if (saveActive(true)) nextVisible(1); });
  elements.exportCheckpoint.addEventListener("click", () => exportJson(false));
  elements.exportFinal.addEventListener("click", () => exportJson(true));
  render();
}());
