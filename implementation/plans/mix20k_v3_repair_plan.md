<!-- mix20k_v3_repair_plan.md - 외부 MIX20K-v3 검수 후보의 감사·보정·비학습 승격 경계를 기록한다. -->

# MIX20K-v3 검수 후보 인수·보정 계획

- 기준일: 2026-08-31
- 외부 후보: `saju-mix20k-v3-review-ready`
- 원본 상태: `3.0.0-review-candidate`
- 보정 후보: `v3.0.1-repaired`
- 모델: `kakaocorp/kanana-2-1.3b-instruct@bf4786aa2a1908adce942d53976270132732f720`
- 결론: 기술 preflight 통과, 학습·production 승격 차단

## 범위와 불변 경계

외부 패키지의 review JSONL을 부모 정본으로 읽고 원본 파일은 수정하지 않는다. 보정 review·Trainer projection·검수 큐는 Git 제외 private build로 새로 만들며, Git에는 코드·계약·원문 없는 집계 보고서만 둔다. AI Hub #86 파생 3,200행은 `restricted_local_only=true`로 전파하고 외부 안전 큐에서 제외한다. 기존 MIX20K-v2, KI20 run, checkpoint와 봉인 blind payload는 수정하거나 읽지 않는다.

이번 단계는 `.train()`, backward, optimizer step, checkpoint 생성을 호출하지 않는다. `training_promotion_allowed`, `production_promotion_allowed`, `phase5_training_performed`는 모두 `false`다.

## 원본 무결성과 실제 감사 결과

패키지 `SHA256SUMS` 15개를 모두 대조했다.

| 입력 | SHA-256 |
|---|---|
| review 20K | `b8c405df67d1a429784cdd9ea5ce10071d3c423627a43e3fba32908046fd1d26` |
| Trainer projection 20K | `c78f3897c4fd2bc5ab5be9e2e63f8999963ce627433bc4eed2155e6abf4cb639` |
| source build manifest | `7c431f8a6f47d0bea422fa144952559fc6c5db60c478d37107ea8e08e193a935` |
| `SHA256SUMS` | `f6dea14385fa3c0ce0c6f3b07ad7a6a1aaf267e69e1a6bcb9b36388530585384` |

원본 20,000행과 15개 축 수량, review→Trainer projection은 일치했다. 구조 감사에서 추가로 확인한 핵심 문제는 다음과 같다.

- 미등록 `kr-saju-v1` model argument 5,250행, argument leaf provenance 0행
- source reference 누락 13,652행
- 명시 입력에서 근거를 찾을 수 없는 chart call 1,838행과 period call 900행
- runtime 기준시각이 없는 상대 기간 638행
- `synthetic_cached_tool_result` 2,000행의 잘못된 `HARD_GT`
- 명백한 한자 조사 오류 4,437개
- 원본 tool result 2,200행 중 `ok` 1,800, `partial` 100, `error` 300
- 외부 validator가 놓친 기간 500행의 hard fact 유실 가능성

외부 문서의 canonical 재검산 대상 2,100행을 그대로 신뢰하지 않았다. cached fixture 2,000행도 검산 전 Gold가 아니므로 최종 `HARD_CANDIDATE`는 3,800행으로 보수적으로 확대했다.

## 구현한 계약과 보정

### Runtime·tool 경계

- `calculate_saju_chart`, `calculate_saju_period`의 단일 strict schema를 학습·runtime·평가가 공유한다.
- 상세 enum, 날짜·시각·IANA timezone, 좌표, nested object와 교차 필드 규칙은 executor가 fail-closed로 검증한다.
- model은 policy/version/request ID를 만들지 않으며 executor가 `saju-tools-v1`, `saju-calculation-policy-v1.0.0`을 주입한다.
- 내부 tool result는 session/review provenance에 보존하고, model에는 `status`, `hard_facts`, `fact_authority`, 오류·제한 필드만 allowlist로 전달한다.
- `partial` 상태를 session 계약에 추가했고 성공·부분·오류 완료 표현을 분리한다.
- 상대 날짜는 runtime 기준시각과 timezone으로 정규화하며 일요일의 `이번 주말`이 한 주 더 밀리던 경계를 수정했다.
- 음력 날짜는 Gregorian 윤년 규칙으로 거절하지 않고 음력 월·일 범위로 구조 검증한다.

### 데이터 보정

- 모든 5,250 tool call을 새 strict schema로 변환하고 leaf provenance를 기록했다.
- model argument와 model-visible message에서 구 정책을 제거했다.
- tool-result 해석 3,200행을 질문 범위에 필요한 hard fact로 다시 grounding했다.
- 기간 500행의 `date/ganzhi`, `days`, `year_month/month_ganzhi`, `year/year_ganzhi`를 보존하고 답변의 날짜·간지를 전수 대조했다.
- cached fixture를 포함한 3,800행을 `HARD_CANDIDATE`로 격하해 canonical engine 재검산 전 학습을 막았다.
- 비협조적 slot 회복 600 trajectory와 assistant 5-turn 상태형 600 trajectory를 추가해 5-turn 이상을 1,000행으로 맞췄다.
- 한자 조사 오류와 stateful `왜` 답변의 새 근거 추가를 자동 보정했다.
- 4,000행 내부 검수 큐를 expert 1,500, canonical engine 800, workflow 625, restricted empathy 700, policy 375로 서로 겹치지 않게 만들었다.
- restricted 원문이 없는 별도 external-safe 큐 4,000행을 만들었지만, 이것은 원천 라이선스상 재배포 승인이라는 뜻이 아니다.

## 최종 불변 산출물

| 구분 | ID·경로 | 검증값 |
|---|---|---|
| private 보정 build | `data/derived/saju_1b_baseline/mix20k-v3.0.1-repaired/build-94eb7b543490` | build SHA-256 `94eb7b5434907539d7041fc81846169dc2e80f332e99b53d710722dcd5564454`, manifest `eca6a9b53f8e29501aab700e9c984071a9e800348d757c1294ea5f80e7937948` |
| public intake | `data/reports/saju_1b_baseline/mix20k-v3-intake/v3.0.0/intake-99c0b48231d6` | intake SHA-256 `99c0b48231d6fe864fdede1bad988f862d09b046c92403a35ed0c86256bac071`, manifest `f58c136764760d2cf7ca34522c666e714bd155ab544cb2964726b0e9f51d8a89` |
| private 비학습 preflight | `data/derived/saju_1b_baseline/mix20k-v3.0.1-preflight/v1.0.0/preflight-aea1c001126e` | manifest `9f5177040dbc32c572bc01a7d651aa01d441d39b77a35e8a4762c08b0052832f` |
| public 비학습 preflight | `data/reports/saju_1b_baseline/mix20k-v3-preflight/v1.0.0/preflight-aea1c001126e` | manifest `c32c3668dfedbc1e800054b1990c9f9531a517ae86444a47a158d61a77b8fca7` |

private build는 review·training 각 20,000행, diagnostic 2,000행, 내부/external-safe 검수 큐 각 4,000행을 가진다. restricted 3,200행은 private에만 존재한다. public 보고서는 원문·개별 ID 없이 집계만 포함한다.

## Gate 결과

| Gate | 상태 | 결과 |
|---|---|---|
| A 파일 무결성 | 통과 | 15개 SHA, 20K 행·ID·projection 일치 |
| B 구조 | 통과 | 축·role·tool ordering·schema·provenance 오류 0 |
| C canonical calculation | 차단 | `HARD_CANDIDATE` 3,800행 재계산 미완료 |
| D exact tokenization | 통과 | 20K 최대 767/768, 초과 0, serialization·mask·EOS 오류 0 |
| E parser | 조건부 통과 | 내부 Kanana XML 5,250/5,250 round-trip; SGLang 실서버 미설치·미검증 |
| F state transition | 부분 통과 | schema·정정 invalidation·상대 날짜·5-turn 구조 검증, 제품 state evaluator 전수 평가는 미완료 |
| G grounding | 부분 통과 | 기간 500행 오류 0, tool-result 3,200행 재grounding; 전체 entity validator 확장은 후속 |
| H 언어·전문 검수 | 차단 | 4K 큐와 expert 1.5K 결정 미완료 |
| diversity | 차단 | exact message 최대 102, target 최대 intake 400·stateful 271; 임의 자동 패러프레이즈 금지 |
| sealed blind | 보존 | hash-only 350 component와 부모·보정 content overlap 0, payload 미열람 |

`train_candidate=true` 10,125행은 행 단위 blocker가 없는 기술 후보일 뿐 학습 승인 집합이 아니다. diagnostic 2K도 496행이 canonical 재검산 등으로 막혀 있어 실행 플래그는 `false`다.

## 공식 문서 대조와 serving 결정

- [Kanana 2 1.3B Instruct 모델 카드](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct)는 `transformers >= 4.57`, remote code, SGLang `0.5.1`, Triton/FA3, `qwen3_coder`를 권장한다. 따라서 SGLang 0.5.1 + `qwen3_coder`를 1차 serving 후보로 고정했다.
- [TRL SFTTrainer](https://huggingface.co/docs/trl/sft_trainer)는 conversational `messages`, assistant `tool_calls`, `tool` role response와 `tools` JSON schema를 함께 요구하고 `assistant_only_loss`에는 generation mask가 필요하다고 명시한다. 보정 projection은 이 네 항목을 유지하고 실제 tokenizer mask를 직접 검증했다.
- [SGLang server arguments](https://github.com/sgl-project/sglang/blob/main/docs/advanced_features/server_arguments.md)는 `qwen3_coder` parser를 지원한다.
- [vLLM 최신 tool-calling 문서](https://docs.vllm.ai/en/latest/features/tool_calling/)는 Qwen3-Coder XML parser 이름을 `qwen3_xml`로 설명하지만 Kanana 모델 카드는 vLLM에도 `qwen3_coder`를 적었다. 이름·버전 차이가 있으므로 vLLM은 secondary 진단 후보로만 두고 production Gate로 쓰지 않는다.

현재 preflight는 실제 SGLang/vLLM package를 설치하거나 model weight를 로드하지 않았다. 고정 tokenizer·chat template와 프로젝트 내부 parser의 문법 round-trip만 검증했다.

## 다음 진행 순서

1. 승인할 canonical birth/period engine과 정책 버전을 고정하고 3,800행을 전수 재계산한다.
2. exact target 반복을 의미 보존 패러프레이즈·상태 조합 확장으로 낮추고, 자동 ID 삽입 같은 가짜 다양화는 사용하지 않는다.
3. 새 부모 hash로 20K를 다시 build하고 tokenizer·mask·tool·grounding preflight를 재실행한다.
4. internal 4K를 workflow/정책/언어 검수하고 최소 expert 1.5K를 별도 Gold로 확정한다.
5. chart/input/template/source group 우선으로 dev·sealed blind를 나누고 기존 blind와 hash-only 누수 검사를 반복한다.
6. 모든 blocker가 닫힌 뒤에만 2K diagnostic을 실행한다. 통과하면 동일 K0에서 v3 10K와 v3 20K를 독립 run으로 비교한다.

이번 구현에서는 2K/10K/20K 학습, 기존 KI20 이어학습, checkpoint 생성, registry 학습 승인 포인터 변경을 하지 않았다.

## 진행 기록

- 2026-08-31
  - 작업 요약: 외부 v3 review candidate를 원본 불변으로 감사하고 runtime/tool/state 계약, 품질 보정판, 검수 큐와 exact-tokenizer 비학습 preflight를 구현했다.
  - 변경 범위: private `build-94eb7b543490`, public `intake-99c0b48231d6`, preflight `preflight-aea1c001126e`를 새 경로에 생성했다. public intake identity에 private build fingerprint를 포함하고 cross-link를 검증한다. AI Hub 제한 행과 model/checkpoint는 Git에 추가하지 않았다.
  - 검증: 20K 전수 최대 767/768, 길이·mask·EOS·serialization 오류 0, 5,250 tool round-trip 오류 0, 기간 grounding 오류 0, blind hash overlap 0을 확인했다.
  - 남은 이슈·후속 작업: canonical 3,800행, exact target 다양성, 전체 state/grounding, 4K·expert 1.5K 검수와 실제 serving parser 검증이 남았다. 모든 학습·production 승인 플래그는 `false`다.
