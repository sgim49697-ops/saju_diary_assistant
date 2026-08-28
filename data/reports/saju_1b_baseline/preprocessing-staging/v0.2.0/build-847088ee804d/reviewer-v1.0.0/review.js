// review.js - loopback API에서 staging 검수 표본을 읽고 판정을 저장한다.
(() => {
  "use strict";
  const state = { bootstrap: null, axis: "all", selected: null };
  const queue = document.querySelector("#queue");
  const recordRoot = document.querySelector("#record");
  const template = document.querySelector("#record-template");

  const label = (axis) => axis === "bazi_sft" ? "BaZi 규칙 렌더링" : "YEJI 신살 파생";
  const visible = () => state.bootstrap.items.filter((item) => state.axis === "all" || item.axis === state.axis);

  function renderQueue() {
    queue.replaceChildren();
    for (const [index, item] of visible().entries()) {
      const li = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = item.decision ? "done" : "";
      if (item.id === state.selected) button.classList.add("active");
      button.textContent = `${String(index + 1).padStart(3, "0")} · ${item.title}`;
      const small = document.createElement("small");
      small.textContent = `${label(item.axis)} · ${item.decision || "미검수"}`;
      button.append(small);
      button.addEventListener("click", () => openRecord(item.id));
      li.append(button);
      queue.append(li);
    }
    const completed = state.bootstrap.items.filter((item) => item.decision).length;
    document.querySelector("#progress").textContent = `전체 ${state.bootstrap.items.length}건 · 판정 ${completed}건`;
  }

  async function openRecord(id) {
    const response = await fetch(`/api/record?id=${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("레코드를 불러오지 못했습니다.");
    const record = await response.json();
    state.selected = id;
    renderQueue();
    const fragment = template.content.cloneNode(true);
    fragment.querySelector(".axis").textContent = label(record.mix_axis);
    fragment.querySelector(".title").textContent = record.review_title;
    fragment.querySelector(".status").textContent = record.current_decision?.decision || "미검수";
    const messages = fragment.querySelector(".messages");
    for (const message of record.messages) {
      const item = document.createElement("div");
      item.className = `message ${message.role}`;
      const strong = document.createElement("strong");
      strong.textContent = message.role;
      item.append(strong, document.createTextNode(message.content));
      messages.append(item);
    }
    const facts = fragment.querySelector(".facts");
    for (const [key, value] of Object.entries(record.review_meta)) {
      const dt = document.createElement("dt");
      const dd = document.createElement("dd");
      dt.textContent = key;
      dd.textContent = Array.isArray(value) ? value.join(", ") : String(value);
      facts.append(dt, dd);
    }
    const form = fragment.querySelector("form");
    if (record.current_decision) {
      const radio = form.querySelector(`[value="${record.current_decision.decision}"]`);
      if (radio) radio.checked = true;
      form.elements.note.value = record.current_decision.note || "";
    }
    const saveState = form.querySelector(".save-state");
    if (state.bootstrap.read_only) {
      for (const control of form.elements) control.disabled = true;
      saveState.textContent = "사용자 일괄 위험 수용으로 승인됨 · 읽기 전용";
    } else {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        saveState.textContent = "저장 중…";
        const body = { id, decision: new FormData(form).get("decision"), note: form.elements.note.value };
        const saved = await fetch("/api/decision", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": state.bootstrap.csrf_token },
          body: JSON.stringify(body),
        });
        if (!saved.ok) {
          saveState.textContent = "저장 실패";
          return;
        }
        const value = await saved.json();
        const queueItem = state.bootstrap.items.find((item) => item.id === id);
        queueItem.decision = value.decision;
        saveState.textContent = "저장됨";
        renderQueue();
      });
    }
    recordRoot.replaceChildren(fragment);
  }

  async function start() {
    const response = await fetch("/api/bootstrap", { cache: "no-store" });
    if (!response.ok) throw new Error("검수 정보를 불러오지 못했습니다.");
    state.bootstrap = await response.json();
    const reviewState = state.bootstrap.read_only ? "사용자 위험 수용 승인 · 읽기 전용" : "검수 진행 중";
    document.querySelector("#build-meta").textContent = `${state.bootstrap.staging_version} · ${state.bootstrap.build_id} · ${reviewState} · 학습 승격 차단`;
    document.querySelectorAll(".filter").forEach((button) => button.addEventListener("click", () => {
      document.querySelectorAll(".filter").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.axis = button.dataset.axis;
      renderQueue();
    }));
    renderQueue();
  }

  start().catch((error) => { recordRoot.textContent = error.message; });
})();
