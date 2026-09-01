<!-- grounded_dialogue_eval_plan.md - 계산기·FSM을 붙인 모델 해석 품질 진단 레인 설계 초안. -->

# 계산기 연결 해석 품질 진단 레인 (초안 뼈대)

- 문서 버전: `grounded-dialogue-eval-v0.1.0`
- 상태: **뼈대만 작성. 로컬 보완 필요**
- 성격: 진단 전용 inference lane. runtime Gate·release·앱 연결·학습 상태를 바꾸지 않는다

---

## 1. 이 실험이 답하려는 질문

`stateful-gate-f5b76dde1921` 결과에서 KI20의 `required_handoff_action`은 **14/100**이었다. 즉 모델이 계산기를 거의 부르지 않는다. 그래서 지금까지 **"진짜 명식을 주면 이 모델이 해석을 제대로 하는가"는 한 번도 측정된 적이 없다.** 호출 실패에 가려져 있었다.

계산기를 그냥 붙여서 자유 호출로 다시 재면 같은 14%를 다시 재게 된다. 계산기가 정확해도 모델이 부르지 않으면 아무것도 달라지지 않는다.

그래서 **호출 결정을 모델에서 실행기로 옮긴 뒤** 해석만 따로 잰다.

```
지금 재는 것 : 모델이 계산기를 부르는가          14%, 이미 앎
이 레인      : 사실을 주면 그것을 존중하는가      미측정
```

## 2. 층 분리

FSM에게 대화를 운전시키면 ARS가 된다. FSM은 대화가 아니라 **사실의 가용성**만 관리한다.

```
대화    모델      무슨 말을 할지, 톤, 공감, 언제 사주를 꺼낼지
사실    계산기    명식·십신·기간 간지. 모델이 만들 수 없는 것
라우팅  FSM       슬롯이 찼나. 지금 계산기를 부를 때인가
```

이 구조에서 모델은 오히려 자유로워진다. 원국을 지어낼 필요가 없으니 대화에만 집중하면 된다. 현재 `no_fabricated_four_pillars`가 84%인 것은 "사주 얘기를 해야 하는데 명식이 없다"는 압박과도 무관하지 않다.

## 3. 작성한 뼈대

| 경로 | 상태 | 내용 |
|---|---|---|
| `configs/runtime/dialogue/fsm_policy-v0.1.0.json` | 완성 | 상태·행동·우선순위·제약 계약 |
| `configs/evaluation/grounded_dialogue_eval-v0.1.0.json` | 완성 | arm 4종·지표 임계·예산 근거 |
| `scripts/runtime/dialogue/states.py` | 완성 | 상태·행동 어휘, 슬롯 정의 |
| `scripts/runtime/dialogue/policy.py` | **핵심 구현** | `classify_state`, `decide` 결정론적 전이 |
| `scripts/evaluation/grounded_dialogue/graders.py` | 일부 | `fabricated_pillars` 구현, 나머지 시그니처 |
| `scripts/evaluation/grounded_dialogue/harness.py` | 뼈대 | arm 실행 루프 골격과 Protocol |

`decide()`는 전역 함수다. 어떤 상태 조합에서도 행동 하나를 반환하며 모델이 개입하지 않는다. 우선순위는 다음과 같다.

```
tool_status ∈ {error, blocked, partial}  → model_limited_reply
saju_intent == false                     → model_free_reply      (사주 강요 금지)
chart_ready                              → model_grounded_reply
birth_input_ready | chart_invalidated    → call_calculator       (실행기가 직접)
그 외                                     → model_ask_missing_slot
```

## 4. 로컬 보완 지점

`TODO(local)` 주석으로 표시해 뒀다.

1. **`policy.build_prompt()`** — `constraint_id`별 지침 블록과 `hard_facts` 직렬화를 예산 안에서 조립한다. 투영은 `scripts/runtime/calculation/bridge.execute_runtime_tool`의 model-visible allowlist를 그대로 쓰고 새 필드를 만들지 않는다. 예산 초과 시 오래된 대화 턴부터 버린다.
2. **`SlotExtractor` 구현 2종** — `rule`(정규식)과 `model_narrow`(모델에게 추출만 시킴). 후자는 "언제 도구를 부를지" 판단보다 훨씬 좁은 과제라 1.3B에도 부담이 적다. 어느 쪽이 나은지가 arm A1 대 A3다.
3. **`harness.run_case()`** — 아래 루프를 채운다. 생성 백엔드는 새로 만들지 말고 `phase5_stateful_chat_gate`의 로더·생성 계약을 감싼다. `do_sample=False`, `num_beams=1`을 유지해야 재현된다.
4. **나머지 채점기** — `fact_contradictions`, `false_completion`, `provided_field_reask`.
5. **`build_report()`** — 기존 리포트 규약(`build_manifest`, `implementation_sha256`, 원문 비포함)에 맞춘 `aggregate.json`.

```
for 사용자 발화 in case.turns:
    슬롯 추출 → session_state 갱신
    decision = decide(session_state, saju_intent=..., last_tool_status=...)
    if decision.action is CALL_CALCULATOR:
        internal, visible = calculator.calculate_chart(...)
        session_state["hard_facts"] = visible.get("hard_facts")
        decision = decide(...)          # 사실 확보 후 재판정
    messages = build_prompt(decision, session_state, arm)
    output = model.generate(messages, max_new_tokens=...)
    기록(turn, decision, visible, output)
```

## 5. 실험 arm과 각 대조가 답하는 것

| arm | 모델 | 예산 | 시스템 프롬프트 | 추출 |
|---|---|---:|---|---|
| A0 | KI20 | 768 | terse_v1 | rule |
| A1 | KI20 | 2048 | full_v1 | rule |
| A2 | K0 | 2048 | full_v1 | rule |
| A3 | KI20 | 2048 | full_v1 | model_narrow |

- **A0 → A1**: 예산과 제대로 된 시스템 프롬프트만으로 얼마나 달라지는가
- **A2 → A1**: **20K 학습의 순효과.** 이 대조가 없으면 파인튜닝이 해석에 기여했는지 알 수 없다
- **A1 → A3**: 슬롯 추출을 규칙으로 할지 모델에 맡길지

A2가 특히 중요하다. K0가 A1과 비슷하면 현재 MIX 데이터가 해석 품질을 올리지 못했다는 뜻이고, v3.1 재학습 계획 자체를 다시 봐야 한다. 학습이 필요 없어 비용도 거의 들지 않는다.

## 6. 지표

FSM이 호출을 맡으므로 `required_handoff_action`은 구조적으로 100%가 된다. 지표는 전부 **사실 충실도**로 옮긴다.

| 지표 | 임계 | 비고 |
|---|---:|---|
| `fabricated_pillars` | 0 | hard_facts에 없는 간지 등장. 최우선 |
| `unknown_hour_violations` | 0 | `hour=null`인데 시주 언급. 가장 날카로움 |
| `fact_contradictions` | 0 | 일간·오행 개수 등을 다르게 단정 |
| `false_completion` | 0 | 도구 실패인데 완료로 말함 |
| `provided_field_reask` | ≤ 5% | KI20 기준선 18% |

**자연스러움은 자동 채점하지 않는다.** 신뢰되는 자동 지표가 없다. stratum별 5건씩 결정론적으로 표본을 뽑아 사람이 본다.

## 7. 토큰 예산에 대한 기록

768은 임의값이 아니다. `mix20k_v3_repair_plan.md`에 "20K 최대 767/768, 초과 0"으로 남아 있고, `phase-4-preflight-validation.md`가 1024를 "실데이터를 더 수용하지 않는 padding-only 진단"으로 판정했다. **학습 데이터 최대 길이에 맞춘 값이며 VRAM 제약이 아니다.**

다만 그 데이터는 단발 샘플이었다. 대화 + 계산기 사실 + 제대로 된 시스템 프롬프트를 담으려면 1,200~2,300 토큰이 필요하다.

- 모델 네이티브 컨텍스트 **4,096** (`original_max_position_embeddings`)
- 확장 32,768은 YaRN이며, Phase 4에 미해결 경고가 남아 있다 → **쓰지 않는다**
- **2,048 우선, 4,096 상한**

**학습 길이와 추론 길이는 달라도 된다.** 768로 학습한 모델을 2,048로 서빙해도 네이티브 범위 안이라 깨지지 않는다. 그래서 A0 대 A1은 재학습 없이 지금 잴 수 있다. 예산만 올리는 재학습은 하지 않고, v3.1 데이터가 실제로 길어질 때 그 분포를 재서 함께 정한다.

## 8. 범위 가드

- KI20 weight·run manifest·checkpoint를 수정하지 않는다 (읽기 전용)
- sealed blind에 접근하지 않는다
- runtime Gate·release registry·앱 연결 상태를 바꾸지 않는다
- **계산기는 승인 전 후보 상태로 충분하다.** 여기서 재는 것은 계산기가 아니라 모델이다. strict gate 통과를 기다릴 이유가 없다
- Phase 6 자동 기술평가(`phase6_technical.py`, `formal_max_length=768`, 봉인 경로)와는 별개 레인이다. 서로의 계약을 건드리지 않는다

## 진행 기록

- 날짜: 2026-09-01
- 작업 요약: 계산기·FSM 연결 해석 품질 진단 레인의 계약과 코드 뼈대를 작성했다. 실행 코드는 로컬 보완 대상으로 `TODO(local)`에 표시했다.
- 변경 범위: 정책 계약 2건, 정책 루프 코드 4건, 진단 하네스 3건, 뼈대 테스트 1건, 이 문서.
- 검증 명령/결과:
  - `python3 -m unittest tests.test_grounded_dialogue_skeleton`: 6건 통과. `decide()` 전역성을 슬롯·의도·도구 상태 160조합으로 확인했다.
  - `uvx ruff check scripts/runtime/dialogue scripts/evaluation/grounded_dialogue tests/test_grounded_dialogue_skeleton.py`: 통과.
  - `git diff --check`: 공백 오류 없음.
  - 전체 unittest는 이 환경에 학습·데이터 의존 패키지가 없어 실행하지 못했다. 로컬에서 재확인이 필요하다.
- 남은 이슈/후속 작업: `build_prompt`, `SlotExtractor` 2종, `run_case`, 채점기 3종, `build_report`가 미구현이다. KI20·sealed blind·runtime Gate는 건드리지 않았다.
