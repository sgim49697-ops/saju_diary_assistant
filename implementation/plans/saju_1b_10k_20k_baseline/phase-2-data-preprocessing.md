# Phase 2. 데이터 전처리·Split·혼합

| 항목 | 값 |
|---|---|
| 실행 상태 | 완료 |
| 선행 Phase | Phase 1 완료 |
| 입력 | source bundle `v1.1.0/build-9462ec148dcd`, 승인 audit `v1.2.0/build-ca756f3eb89f`, 고정 원본·교정·한국어 문구 은행 |
| 현재 출력 | MIX20K 전용 24K staging `v0.1.0/build-109815ee6879`, 공개 집계·검수 HTML·사용자 일괄 위험 수용 기록 |
| Phase 4 인계 | 정확한 MIX20K·중첩 MIX1K·MIX10 manifest, holdout/core eval, tokenizer·누수·모델 preflight |
| 완료 Gate | 승인 audit 부모의 결정론적 24K staging·검수·인계 계약 통과. `training_promotion_allowed=false` 유지 |
| 웹 확인일 | 2026-08-28 |

## 목적

소스별 원문을 재현 가능한 공통 대화 스키마로 변환하고, 승인 audit를 부모로 한 MIX20K용 24K 후보 순서를 고정한다. 외부 원문을 곧바로 섞지 않고 허용된 변환 사슬과 안전성 Gate를 통과한 행만 후보로 만든다. tokenizer에 의존하는 평가 split·정확한 20K·10K·1K manifest·학습 승격은 Phase 4에서 수행한다.

## Phase 2A. 전처리 전 원천 감사

Phase 2A는 원본을 수정하지 않는다. 네 원천 전체의 구조·중복·누수·안전 flag를 집계하고, 비공개 locator로만 구성된 검토 큐를 만든다. 현재 정본은 2026-08-27 사용자의 명시 지시에 따라 핵심 150건과 참고 150건을 모두 수용했지만, 항목별 명리 전문 검수가 수행된 것으로 기록하지 않는다. decision provenance는 `owner-blanket-risk-acceptance-v1.0.0`, `domain_item_review_performed=false`로 고정한다.

```text
data/raw/<source>/<upstream-revision>/
data/audit/saju_1b_baseline/v1.2.0/build-ca756f3eb89f/       # Git 제외·seal 후 읽기 전용
data/reports/saju_1b_baseline/audit/v1.2.0/build-ca756f3eb89f/
data/reports/saju_1b_baseline/audit-review/v1.2.0/build-ca756f3eb89f/reviewer-v1.1.0/
data/staging/saju_1b_baseline/v0.1.0/build-109815ee6879/     # Git 제외·24K
data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/
```

source build는 네 원천 manifest hash, audit build는 source build·감사 정책·seed 42·감사 코드 hash로 계산한다. 절대경로와 실행 시각은 fingerprint에서 제외하고 전체 SHA-256을 manifest에 기록한다. 기존 build는 덮어쓰지 않으며 동일 입력 재실행은 무결성 검증만 한다.

```bash
.venv-data/bin/python scripts/data/phase2_verify_history.py --audit-version v1.2.0 --build build-ca756f3eb89f
.venv-data/bin/python scripts/data/phase2b_preprocess.py plan
.venv-data/bin/python scripts/data/phase2b_verify_history.py --staging-version v0.1.0 --build build-109815ee6879
.venv-data/bin/python scripts/data/phase2b_review_web.py --build build-109815ee6879 --port 8765
```

승인된 과거 build는 registry의 `implementation_commit`과 artifact hash를 기준으로 `*_verify_history.py`가 검증한다. 일반 `phase2_audit.py verify`와 `phase2b_preprocess.py verify`는 현재 설정·코드로 새 build를 만들 때 사용하며, 현재 fingerprint를 과거 build에 억지로 대입하지 않는다.

원천 감사 HTML은 핵심 150건과 참고 150건, 전처리 HTML은 BaZi 150건과 YEJI 150건을 제공한다. HTML 파일을 직접 열지 않고 loopback 서버로 접속한다. 서버는 `127.0.0.1`에만 bind하며 Host·Origin·CSRF, CSP, no-store, 16KiB 본문 제한을 강제한다. 학습 메시지는 Git 제외 staging에서 API로만 읽고 공개 보고서에는 원문 sample을 넣지 않는다.

감사 `v1.2.0/build-ca756f3eb89f`는 accept 300건, 필수 잔여 0건, blocking finding 0건으로 seal·승인·원본 재검증을 통과했다. 다만 이 승인은 항목별 전문 품질 인증이 아니라 자동 검사 결과에 대한 사용자 위험 수용이다. 향후 실제 전문가 판정을 추가할 때는 이 사실을 덮어쓰지 않고 새 audit/staging 버전으로 만든다.

### 팀원용 핵심·참고 300 advisory 검수본

사용자가 팀원이 AI Hub #86의 동일 신청에 포함됐거나 AI Hub로부터 열람 권한을 명시적으로 확인받았음을 확인한 경우에만 `phase2_export_team_review.py`로 저장소 밖의 오프라인 공유본을 만든다. 단순히 같은 팀이거나 같은 회사에 속한 것은 승인 근거가 아니다. 공유본에는 required 큐 150단위와 reference 큐 150단위, 합계 300단위·340레코드만 원천별 whitelist로 최소 투영한다. AI Hub 원천 ID, Nemotron UUID·생년 좌표, `bazi-sft` ID·`birth_input`, 모든 locator·비공개 판정 원장·개인 메모는 제외한다. YEJI correction 대상은 원본과 적용값을 함께 보여준다.

```bash
.venv-data/bin/python scripts/data/phase2_export_team_review.py build \
  --audit-version v1.2.0 \
  --build build-ca756f3eb89f \
  --confirm-aihub-authorized-reviewer

.venv-data/bin/python scripts/data/phase2_export_team_review.py verify \
  --archive ../saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip

.venv-data/bin/python scripts/data/phase2_export_team_review.py verify-feedback \
  --archive ../saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip \
  --feedback /승인된/내부/경로/team-review-build-ca756f3eb89f-final.json
```

생성기는 동일 승인 범위 확인 flag, 저장소 밖 `.zip` 경로, 기존 파일 무덮어쓰기, 내부 파일 0600 권한, 고정 파일 목록·수량·projection fingerprint·SHA-256을 강제한다. ZIP은 사용자 선택에 따라 암호화하지 않았으므로 승인된 내부 채널로만 전달한다. 팀원은 압축을 모두 푼 뒤 `START_HERE.html`에서 검수하고 checkpoint/final JSON과 편의용 CSV만 반환한다. 의견은 `advisory_team_review`로 묶이며 `verify-feedback` 통과 후에도 본 판정 ledger로 자동 변환하거나 합치지 않는다. 원 담당자가 해당 `review_id`의 원문과 의견을 다시 확인해 기존 loopback 검수기에서 최종 판정한다.

생성된 현재 공유본은 `/home/user/projects/saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip`이며 핵심·참고 300단위, 340레코드를 담는다. ZIP SHA-256은 `b28da2d8cf1685e00fc7a73f4401a2282a006fcf34885581335c103876c6984a`다. ZIP과 sidecar는 Git 추적 대상이 아니며, 위의 명시적 열람 권한을 확인하기 전에는 전달하지 않는다.

### Phase 2A 전체 스캔 결과

| 검사 | 결과 |
|---|---:|
| Nemotron | 1,000,000행, canonical 명식 266,950개, invalid 명식 0 |
| `bazi-sft` | 100,000행, synthetic group 25,000개, 일간·오행 검산 실패 0 |
| AI Hub #86 | 58,268건, 감정 type 60개, 고유 talk group 51,886개 |
| YEJI | 51규칙, ID·원천 이름 대조 완료, overlay 5건 적용 후 구조 실패 0 |
| Nemotron↔BaZi 동일 명식 | 8,347개 |
| 필수/참고 검토 큐 | 150/150 단위, 모두 사용자 일괄 위험 수용 |

필수 검토는 안전 우선으로 AI Hub 70, Nemotron 40, `bazi-sft` 20, YEJI 20단위다. 참고 큐는 AI Hub 30, Nemotron 50, `bazi-sft` 40, YEJI 30단위다. 공개 보고서는 집계와 finding code만 포함하며 원문, 원천 record ID, locator, 명식 hash, 비공개 메모를 포함하지 않는다.

과거 v1.0에서 다음 두 finding이 blocking 상태였다.

- `YEJI_CIGUAN_CONFLICT`: `词馆`의 金 정학당이 주석 `壬申`과 코드·JSON `壬卯`로 충돌한다.
- `YEJI_STRUCTURE_FAILURE`: `五鬼`가 상위 category 목록에 없는 `흉살류`를 사용한다.

감사 v1.2는 원본 파일과 원천 manifest를 바꾸지 않고 `yeji-rule-corrections-v1.2.0.json`의 exact expected-value overlay만 적용한다. `词馆`의 `壬卯→壬申`, `五鬼` category, 덕수귀인 조건 설명·매핑, 동자살의 계절/납음 OR 조건을 고정 upstream 코드에 맞춰 다섯 항목으로 교정했다. 원본 예상값이 달라지면 overlay는 즉시 실패한다.

## Phase 2B. MIX20K 전용 24K staging

전체 100만+ 원천을 모두 한국어 학습행으로 변환하지 않는다. 대표 후보 선택과 규칙 검증에는 전체 원천을 읽되 실제 정제 산출물은 최종 MIX20K와 source별 예비 20%를 합친 24,000건만 만든다. MIX1K·MIX10은 Phase 4에서 고정 tokenizer를 통과한 MIX20K의 중첩 부분집합으로 확정한다.

| 축 | 최종 20K 목표 | staging 24K | 구현 결과 |
|---|---:|---:|---:|
| Nemotron | 11,000 | 13,200 | v6 2,640·v7 10,560 |
| `bazi-sft` | 5,000 | 6,000 | 1,500 group × 질문 4종 |
| AI Hub 단일턴 | 2,000 | 2,400 | talk group 2,400 |
| AI Hub 멀티턴 | 1,000 | 1,200 | 단일턴과 group 교집합 0 |
| YEJI 파생 | 1,000 | 1,200 | 51규칙 모두 포함 |

`gpt-5.6-sol` max는 ChatGPT 구독 로그인으로 BaZi 고정 질문·규칙 설명과 YEJI 51개 중립 의미 문구만 생성했다. 자유 번역이나 행별 답변 생성에는 사용하지 않았다. 결과는 schema·51개 ID/name·금지 표현 검사를 거쳐 `language-bank-v1.0.0.json`으로 고정했고 model, CLI, prompt/schema/raw output SHA-256을 provenance에 기록했다.

전처리 build는 Nemotron 1,000,000행, BaZi 100,000행, AI Hub 58,268행을 전수 검사했다. Nemotron 안전·언어 필터 후 적격 761,985행, AI Hub 안전·언어 필터 후 적격 53,768행·48,190 group을 확인했다. BaZi 다섯 규칙 재계산 불일치 0건, YEJI evaluator 미지원 규칙 0개다. 최종 24K는 고유 ID·고유 message hash 각 24,000, 영문 단어 잔여 0건, 지원하지 않는 role 순서 0건이다.

24K 안에서 AI Hub 단일턴·멀티턴 축의 `leakage_group_id` 교집합은 0개다. Nemotron·BaZi·YEJI처럼 명식을 공유하는 서로 다른 축 간에는 동일 전역 `leakage_group_id` 36개가 있다. 이는 중복 오류가 아니라 교차 원천의 같은 명식을 표시한 누수 방지 정보이며, Phase 4는 해당 36개 group을 분할하지 않고 통째로 한 split에 배치한다.

사용자는 BaZi 150건·YEJI 150건을 항목별로 판독하지 않고 모두 통과시키라고 명시했다. 따라서 `APPROVAL.json`은 `explicit_owner_blanket_risk_acceptance`, `domain_item_review_performed=false`, `quality_certification_claimed=false`로 기록한다. 이 승인은 Phase 4 preflight 입력만 허용하며 `training_promotion_allowed=false`를 유지한다.

## 공통 레코드 계약

```json
{
  "id": "source:original_id",
  "source": "nemotron_saju",
  "mix_axis": "nemotron_saju",
  "source_variant": "v6",
  "source_revision": "commit-or-version",
  "license_expression": "CC-BY-4.0",
  "usage_class": "train_allow",
  "provenance_status": "verified",
  "attribution_ids": ["nvidia-nemotron-korea", "rayraykim-nemotron-saju"],
  "transformation_chain": ["source_output", "nfc_normalized", "safety_filtered"],
  "domain": "saju",
  "task": "structured_saju_reading",
  "messages": [
    {"role": "system", "content": "주어진 태스크와 입력에 맞게 한국어로 답하세요."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ],
  "label": {
    "stage": "R0+A1",
    "kind": "auto_validated_synthetic",
    "origin": "source_output",
    "human_review": "not_reviewed"
  },
  "quality_flags": {
    "parse_ok": true,
    "language_ok": true,
    "exact_duplicate": false,
    "translation_residue": false,
    "over_length": false
  },
  "meta": {
    "raw_hash": "sha256...",
    "source_group_id": "...",
    "leakage_group_id": "...",
    "conversation_id": null,
    "chart_signature": null,
    "input_tokens": 0,
    "assistant_tokens": 0,
    "total_tokens": 0
  }
}
```

`id`, `source`, `mix_axis`, `source_revision`, `license_expression`, `usage_class`, `provenance_status`, `attribution_ids`, `transformation_chain`, `task`, `raw_hash`, `source_group_id`, `leakage_group_id`는 null을 허용하지 않는다. 원천을 나타내는 `source` enum은 `nemotron_saju`, `bazi_sft`, `aihub_empathy`, `yeji_bazi_rules` 네 값이다. 학습 할당을 나타내는 `mix_axis` enum은 `nemotron_saju`, `bazi_sft`, `aihub_empathy_single`, `aihub_empathy_multiturn`, `yeji_shensha_derived` 다섯 값이다. 원본 ID가 없으면 source 파일 SHA-256, 행 번호, 정규화 전 원문 SHA-256으로 결정론적 ID를 만든다.

`source_group_id`는 원천 내부 의미 단위를 보존하고, `leakage_group_id`는 train/eval 경계를 결정한다. 명식은 `년간-년지-월간-월지-일간-일지-시간-시지` 순서의 NFC 한자 8자로 정규화한 뒤 `chart:<SHA-256>`을 사용한다. Nemotron, `bazi-sft`, 향후 YEJI 파생본은 원천이 달라도 같은 명식이면 같은 `leakage_group_id`다. AI Hub 단일턴·멀티턴은 모두 `aihub-talk:<SHA-256(talk-id)>`를 사용한다.

## 태스크와 라벨 값

| task | 목적 |
|---|---|
| `structured_saju_reading` | 구조화 원국 기반 풀이 |
| `grounded_rule_reading` | 입력 사실과 명시 규칙에 근거한 풀이 |
| `shensha_rule_qa` | 검증된 신살 조건의 정의·판정·교정 |
| `empathic_response` | 단일 턴 공감 응답 |
| `natural_multiturn_dialogue` | 대화 문맥의 다음 발화 |

| 소스 | `label.kind` |
|---|---|
| Nemotron | `auto_validated_synthetic` |
| `bazi-sft` 파생본 | `validated_rule_soft_gt` |
| AI Hub #86 단일턴 파생 | `human_reference` |
| AI Hub #86 멀티턴 파생 | `human_next_turn` |
| YEJI 신살 규칙 파생본 | `validated_rule_derived` |

`label.stage`는 `R0+A1`, `R0+A2`, `H1`, `H2`, `D`만 쓴다. v1 학습 후보는 `R0+A1` 또는 규칙 검증을 통과한 `R0+A2`이며 가중치를 차등 적용하지 않는다.

## 공통 최소 전처리

1. 원문과 변환 후 원문의 SHA-256을 기록한다.
2. Unicode NFC 정규화, 비정상 제어문자 제거, 연속 공백 정리만 수행한다.
3. 필수 필드 누락, 빈 assistant, 파싱 실패, 심한 문자 깨짐을 제외한다.
4. source 내부 exact duplicate를 제거한 뒤 source 간 exact duplicate를 제거한다.
5. 한국어 정답이 완전히 중국어인 행을 제외한다.
6. 문자 길이와 원문 구조는 기록하되 모델 token 길이 필터는 Phase 4까지 미룬다.
7. 제외된 행은 같은 source·task·문자 길이 구간에서 보충 후보를 남긴다.
8. 변환 이유와 필터 코드를 행별 audit log에 남긴다.
9. 건강 진단·치료 지시, 투자 종목·수익 보장, 죽음·사고·이혼 단정, 차별적 성역할, 자해·위기 상황의 부적절한 조언을 제외한다.
10. 남은 해석은 전통문화·오락적 참고라는 불확실성 표현을 유지하고 입력에 없는 사실을 추가하지 않는다.

다음은 v1에서 수행하지 않는다.

- 자유 생성 LLM 답변 재작성 또는 모델 judge 단독 필터
- 한국식 용어·학파 통일
- 생년월일에서 원국을 생성하는 계산 데이터 제작
- 의미 기반 dedup
- 답변 길이 강제 통일
- 사람 Gold oversampling

## 소스 adapter 계약

### Nemotron

- 사용자 배경과 `saju_pillars`, 일간, 오행, 십신을 고정 템플릿으로 직렬화한다.
- assistant는 `saju_narrative`의 네 키를 `saju_summary`, `personality_reading`, `career_reading`, `lacking_element_advice` 순서로 직렬화한다.
- 네 키와 값, `saju_narrative_error == null`을 요구한다.
- v6/v7을 `source_variant`로 보존하고 문체 통계는 별도로 낸다.
- `birth_datetime_synth`는 provenance에만 보존하고 학습 prompt에 넣지 않는다.
- 건강·재물·직업의 확정적 예측이나 입력 사실과 맞지 않는 문장을 제외한다.
- `source_group_id`는 UUID hash, `leakage_group_id`는 전역 canonical 명식 hash로 만든다. UUID가 달라도 같은 명식이면 split을 넘지 않는다.

### `bazi-sft`

- `facts.pillars`, `day_master`, `element_counts`, `retrieved_rules`, `question_type`만 파생 입력으로 사용하고 `birth_input`은 학습 prompt에서 제거한다.
- 네 기둥에서 일간·오행 수를 다시 계산하고, 각 `retrieved_rules` 조건이 실제 입력에 성립하는지 검증한다.
- 검증된 구조와 Apache 2.0 effect를 승인된 한국어 용어표·고정 template로 재렌더한다. 원본 English `response`의 자유 번역은 사용하지 않는다.
- 같은 `synthetic_id`의 네 question type은 하나의 `source_group_id`로 묶고, canonical 명식 hash를 `leakage_group_id`로 사용해 다른 synthetic ID·다른 원천의 동일 명식도 train/eval을 넘지 않게 한다.
- calendar anchor 정확도가 필요한 주장, 날짜·지역 기반 계산, 검증되지 않은 규칙은 제외한다.

### YEJI 신살 규칙 파생본

- SHA-256이 고정된 `rules/shensha_51.json` 외의 YEJI 파일을 읽으면 실패한다.
- 51개 condition을 원천 `chxb/shensha.js`와 대조하고 허용 stem·branch·60갑자 집합, mapping 내부 일관성을 검사한다.
- `词馆` 원본의 `壬卯`는 그대로 보존하고, 독립 기준으로 확인한 `壬申`을 versioned correction overlay에서만 적용한다. 교정 ID·전후 값·근거·attribution을 파생 행에 남긴다.
- 검증된 rule마다 정의, 조건 판정, 반례, 잘못된 판정 교정 태스크를 구조화 명식 조합으로 생성한다. 날짜·실존 인물은 사용하지 않는다.
- `meaning`은 soft reference로만 쓰며 죽음·질병·재난을 확정하는 문구는 중립적인 전통 해석 설명으로 제한한다.
- `source_group_id = rule_id + chart_signature`, `leakage_group_id = chart:<SHA-256>`로 만들고 동일 명식은 다른 사주 원천과도 split을 넘지 않는다.

### AI Hub #86 단일턴 파생

- 사용자 발화와 대응 시스템 응답을 한 예시로 만든다.
- upstream train/validation 표시는 provenance에만 보존하고 최종 split 경계로 사용하지 않는다.
- 전화번호, 주민번호, 이메일 등 개인정보 패턴이 있으면 마스킹보다 행 제외를 우선한다.
- 감정 label은 meta에만 두고 입력에 정답 감정을 노출하지 않는다.
- 자해·자살·우울증 위기, 임상 진단·치료, 미성년자 민감정보가 포함된 행은 일반 공감 학습 후보에서 제외한다.
- 원본 상황/대화 ID hash를 `source_group_id`와 `leakage_group_id`에 사용한다.

### AI Hub #86 멀티턴 파생

- #86의 동일 대화/상황 그룹 안에서 순서가 확인된 앞 1~4턴을 문맥으로 주고 다음 시스템 응답을 assistant로 만든다.
- 한 세션당 최대 2개 예시만 추출한다.
- 개인정보, 외부 저작물 장문, 비속어·혐오, 의료·법률·금융 조언을 제외한다.
- 세션 ID 단위로만 split하고 전체 세션 ID hash를 `source_group_id`와 `leakage_group_id`로 사용한다.
- `leakage_group_id = aihub-talk:<SHA-256(talk.id.talk-id)>`로 고정하고 upstream train/validation 전체를 합친 group index를 먼저 만든다.
- Phase 1에서 확인한 upstream split 간 group 교집합 6,379개는 한 group으로 묶는다. exact record 교집합은 0개지만 동일 ID를 train/eval에 나누지 않는다.

## Phase 4 인계 계약: 평가셋 우선 분리

이 절은 Phase 2에서 평가셋을 이미 생성했다는 뜻이 아니라, Phase 4가 24K 후보를 승격할 때 반드시 따를 split 계약이다.

### Source holdout 500

다섯 `mix_axis`에서 group 단위로 100개씩 먼저 고정한다. 동일 group의 다른 행은 학습 후보에서 모두 제외하며, #86 단일턴과 멀티턴 축 사이에도 group ID가 겹치면 안 된다.

### Core Eval 200

| 영역 | 수량 |
|---|---:|
| 구조화 원국 풀이 | 45 |
| 근거 규칙 기반 풀이 | 35 |
| 입력 사실 모순·환각 | 35 |
| 신살 규칙 판정·교정 | 20 |
| 동일 명식 반복 질문 일관성 | 20 |
| 공감 응답 | 20 |
| 멀티턴 자연 대화 | 15 |
| 명식 누락 시 계산기 handoff | 5 |
| 일반 한국어 instruction 보존 | 5 |

Core Eval의 원문·정답·채점 정책을 별도 해시로 잠그고, 학습용 adapter가 읽는 경로 밖에 둔다.

## 혼합 후보 순서와 수량 계약

소스별 후보는 seed 42로 한 번만 섞어 결정론적 후보 순서를 만든다. 각 source는 MIX20 목표 수량보다 최소 20% 많은 적격 후보를 확보한다. 20행 구성 단위의 수량 계약은 다음과 같다.

```text
Nemotron                11
bazi-sft                 5
AI Hub #86 단일턴        2
AI Hub #86 멀티턴        1
YEJI 신살 규칙 파생본     1
```

1. 각 source의 문자 길이·variant 층을 유지하며 후보 ID 순서와 reserve pool을 고정한다.
2. 아래 MIX1K·MIX10·MIX20 수량표와 `MIX1K ⊂ MIX10 ⊂ MIX20` 계약을 machine-readable spec으로 저장한다.
3. Phase 4가 고정 tokenizer로 512/768/1024 token 적격 행을 계산한다.
4. Phase 4는 각 길이에서 후보 순서대로 MIX20 수량을 채우고, source별 앞 절반을 MIX10으로 고정한 뒤 MIX10의 앞 10%를 MIX1K로 고정한다.
5. 수량·포함 관계 검증 후 Phase 4가 각 manifest 행 순서를 seed 42로 별도 shuffle한다.

| 소스 | MIX1K | MIX10 | MIX20 |
|---|---:|---:|---:|
| Nemotron | 550 | 5,500 | 11,000 |
| `bazi-sft` 파생본 | 250 | 2,500 | 5,000 |
| AI Hub #86 단일턴 | 100 | 1,000 | 2,000 |
| AI Hub #86 멀티턴 | 50 | 500 | 1,000 |
| YEJI 신살 규칙 파생본 | 50 | 500 | 1,000 |

세 manifest는 위 비율과 수량을 정확히 지키고 MIX1K를 MIX10의 정확한 부분집합으로 고정한다.

## Phase 4로 넘길 길이 감사 입력

Phase 2에서는 tokenizer를 사용하지 않고 다음 문자·구조 통계를 넘긴다.

- source/task별 행 수와 input·assistant 문자 수
- 문자 길이 평균, 중앙값, p90, p95, 최대
- 멀티턴 수와 messages role 분포
- source별 목표 수량 대비 reserve pool 비율
- Nemotron v6/v7, `bazi-sft` question type, 신살 rule category별 문자 길이 분포

token 수, 512/768/1024 초과율, assistant loss token 비율과 special token 중복은 Phase 3의 tokenizer·template가 준비된 뒤 Phase 4에서 감사한다.

## 완료 Gate

- [x] Phase 2A 필수 150건·참고 150건을 사용자 일괄 위험 수용으로 해소했다. 항목별 전문 검수로 간주하지 않는다.
- [x] YEJI 두 blocking finding의 교정 계약을 새 버전으로 고정했다.
- [x] audit `v1.2.0/build-ca756f3eb89f`를 seal하고 registry의 `approved_audit`를 설정했다.
- [x] 24K staging 모든 행이 공통 스키마와 enum을 만족한다.
- [x] 24K staging의 record ID·message exact duplicate가 0건이다.
- [x] Phase 4가 train·holdout/core eval을 group-first로 분리하도록 전역 leakage group 계약과 24K 후보 순서를 인계했다.
- [x] 각 source가 MIX20 목표보다 정확히 20% 많은 24K staging 후보를 가진다.
- [x] seed 42 후보 순서를 고정했고 MIX1K⊂MIX10⊂MIX20 최종 manifest 생성 계약을 Phase 4로 인계했다.
- [x] #86 단일턴·멀티턴 staging 축의 talk group 교집합이 0개다. train/eval 경계는 Phase 4에서 검사한다.
- [x] #86의 안전·언어 적격 group 48,190개에서 멀티턴 1,200개를 확보했다.
- [x] `bazi-sft` 100,000행 구조·규칙 검산과 6,000행 한국어 재렌더가 통과했다.
- [x] 신살 51개 원천 대조·known issue 교정과 허용 파일 검사가 통과했다.
- [x] 의료·투자·운명 단정·자해 위기 및 영문 잔여 필터의 제외 집계를 기록했다.
- [x] 전체 원천은 필터별 집계, 선택 24K는 transformation chain·raw hash·결정론적 후보 순서를 기록했다.
- [x] messages role과 축별 입력·assistant 문자 길이 통계를 Phase 4 입력 보고서에 기록했다.

### Phase 4 전용 후속 Gate

아래 항목은 Phase 2 완료를 차단하지 않고 Phase 4의 `training_promotion_allowed` 판정을 차단한다.

- [ ] train과 holdout/core eval 사이 전역 `leakage_group_id` 중복이 0건이다.
- [ ] 고정 tokenizer로 적격 행을 골라 MIX1K⊂MIX10⊂MIX20 manifest를 확정했다.
- [ ] assistant loss mask·special token·모델 로드·200-step smoke Gate를 통과했다.

## 산출물

```text
data/staging/saju_1b_baseline/v0.1.0/build-109815ee6879/records/<mix-axis>.jsonl
data/staging/saju_1b_baseline/v0.1.0/build-109815ee6879/candidate_order.jsonl
data/staging/saju_1b_baseline/v0.1.0/build-109815ee6879/review_selection.json
data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/aggregate.json
data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/gate.accepted.json
data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/reviewer-v1.0.0/
data/reports/saju_1b_baseline/phase-verification/v1.0.0/review-20260828/

# Phase 4에서만 생성
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/manifests/MIX20K.jsonl
data/derived/saju_1b_baseline/v1.0.0/build-<derived-hash>/eval/<holdout-and-core-eval>
```

## 공식 자료

- [Datasets 현재 버전 cache와 fingerprint](https://huggingface.co/docs/datasets/en/about_cache)
- [DuckDB 1.5 Parquet 읽기](https://duckdb.org/docs/current/data/parquet/overview)
- [DuckDB 1.5 JSON 개요](https://duckdb.org/docs/current/data/json/overview)
- [TRL 1.12.0 dataset formats](https://huggingface.co/docs/trl/v1.12.0/en/dataset_formats)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Datasets 현재 cache/fingerprint 문서 | 원본 Arrow 상태와 변환 hash를 결합하는 fingerprint 원칙 확인 |
| 2026-08-27 | DuckDB current 1.5 Parquet·JSON 문서 | 고정 로컬 Parquet 목록 읽기와 projection/filter pushdown, JSON 처리 방식 확인 |
| 2026-08-27 | TRL conversational format | `messages` 역할·내용 구조와 assistant mask 연계 확인 |
| 2026-08-27 | [chxb/shensha 고정 revision](https://github.com/chxb/shensha/blob/5b90110e55feb92303ef7853ecacdb6f9ed59eac/shensha.js)·[삼명통회 대조](https://www.tianjihq.com/zh-CN/learn/glossary/bazi-ss-xuetang) | `词馆` 金 정사관의 `壬申` 근거를 교차 확인하고 exact overlay 범위를 고정 |
| 2026-08-27 | [Python 3.10 `http.server`](https://docs.python.org/3.10/library/http.server.html)·[OWASP CSRF 방어](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html) | 외부 공개 서버가 아닌 loopback 전용 검수기로 제한하고 Host·Origin·CSRF token·no-store를 적용 |
| 2026-08-27 | [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do) | 미승인 법인·단체·개인에 대한 열람·제공 금지를 재확인하고, 사용자가 동일 승인 범위라고 확인한 팀원에게만 최소 투영본을 내부 전달하도록 제한 |
| 2026-08-28 | [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do) | 단순 팀 소속이 아니라 #86 동일 신청 포함 또는 AI Hub의 명시적 열람 권한이 필요하도록 공유 Gate를 강화 |
| 2026-08-28 | 고정 원천·과거 build 재검증 | 현재 코드 fingerprint와 과거 승인 build를 혼용하지 않고 registry의 당시 commit·manifest·approval·decision hash로 audit v1.0~v1.2와 staging v0.1.0을 독립 검증 |

## 진행 기록

- 2026-08-27
  - 작업 요약: 전처리에 앞선 읽기 전용 Phase 2A 감사, 사용자 검토 150건, 명시적 승인 Gate와 버전별 데이터 경로를 구현했다.
  - 변경 범위: source bundle·audit policy·registry, 감사 CLI·검토 큐·공개 집계 보고서와 공통 leakage group 계약을 추가했다. 원본과 학습 후보는 변경하지 않았다.
  - 검증: 단위 테스트 20건, 전체 원본 재해시, `build-336b8377063a` 전체 스캔·verify, 공개 보고서 원문 금지 검사, 동일 build 무쓰기 재실행, 미검토 finalize·무확인 approve 차단을 통과했다.
  - 남은 이슈·후속 작업: 사용자 필수 검토는 0/150이다. YEJI `YEJI_CIGUAN_CONFLICT`, `YEJI_STRUCTURE_FAILURE`를 교정한 새 build가 승인되기 전에는 adapter·필터·split·파생 QA를 구현하지 않는다.
- 2026-08-27
  - 작업 요약: 두 YEJI finding을 원본 불변 overlay로 교정한 감사 v1.1과 source-aware HTML 검수기를 구현해 Phase 2A 자동 검사 구간을 완료했다.
  - 변경 범위: `v1.1.0/build-e162d9b2b7dc` 비공개 큐·공개 보고서, correction/policy/registry, append-only revision API, 버전별 `audit-review` 정적 자산을 추가했다. Phase 2B adapter와 파생 데이터는 만들지 않았다.
  - 검증: 관련 단위 테스트 20건, Ruff·compile·Node 구문 검사, Phase 1 원본 재검증, v1.1 전체 audit verify와 v1.0 당시 Git 코드 기반 historical verify, API 보안 헤더·locator 비노출, Chromium 화면 검증을 통과했다. 관측 finding 2건은 모두 해소됐고 blocking finding은 0건이다.
  - 남은 이슈·후속 작업: 필수 검수 0/150과 선택 참조 검수 0/151이 남았다. 사용자 검토가 끝난 뒤에만 finalize하고, 별도 명시 승인을 받은 뒤 registry 승인 포인터를 설정한다.
- 2026-08-27
  - 작업 요약: 동일 AI Hub 승인 범위 팀원의 독립 의견을 받을 수 있도록 핵심 150단위 전용 오프라인 HTML 공유 ZIP과 반환 JSON 검증기를 구현했다.
  - 변경 범위: 원천별 최소 투영, 식별자·locator 차단, YEJI 교정 전후 표시, checkpoint/final JSON·CSV 내보내기, ZIP 내부 manifest·SHA-256·0600 권한과 저장소 밖 생성 계약을 추가했다. 참조 151단위와 본 판정 ledger는 포함하거나 수정하지 않았다.
  - 검증: 신규 회귀 테스트 7건과 전체 38건, 변경 파일 Ruff·Python compile·Node 구문 검사, 실제 v1.1 원본 재검증과 150단위·180레코드 archive verify, sidecar SHA-256, Windows Chrome 1600×1000 오프라인 렌더링과 AI Hub 대화 턴 순서를 확인했다.
  - 남은 이슈·후속 작업: 공유본은 암호화되지 않은 통제 데이터이므로 승인된 내부 채널에서만 전달·회수·삭제한다. 팀원 JSON은 advisory 의견으로만 받고 원 담당자가 본 검수기에서 판정을 확정해야 하며, 현재 Gate 상태는 필수 0/150·참조 0/151·미봉인·미승인 그대로다.
- 2026-08-27
  - 작업 요약: Nemotron 전체 100만 행 source bundle과 감사 v1.2를 고정하고, 핵심·참고 각 150건을 함께 제공하는 검수기·내부 공유 ZIP을 갱신했다. 사용자의 명시 지시에 따라 감사 300건을 일괄 위험 수용으로 기록해 seal·승인했다.
  - 변경 범위: source bundle `v1.1.0/build-9462ec148dcd`, audit `v1.2.0/build-ca756f3eb89f`, YEJI 교정 5건, registry와 버전별 보고서·검수 자산을 추가했다. `domain_item_review_performed=false`를 보존했다.
  - 검증: 네 원천 전체 재해시, Nemotron 1,000,000행·UUID 중복 0·빈 행 0, audit 필수/참고 150/150 accept, blocking finding 0, seal·approval·verify를 통과했다.
  - 남은 이슈·후속 작업: 승인 방식은 전문가 항목별 검수가 아니다. 실제 도메인 품질 보증이 필요하면 새 버전에서 별도 검수를 수행한다.
- 2026-08-27
  - 작업 요약: 첫 20K baseline에 필요한 분량만 정제하도록 최종 20K + 예비 20%인 24K staging 파이프라인을 구현했다. BaZi 100K 규칙 전수 검산, YEJI 51규칙 evaluator, AI Hub group 분리, Nemotron v6/v7 20:80 선별과 한국어 잔여 Gate를 적용했다.
  - 변경 범위: `phase2b_preprocess.py`, 원천별 adapter·검증기·승인 후 읽기 전용이 되는 loopback 검수 서버, 한국어 문구 은행과 staging `v0.1.0/build-109815ee6879`, 공개 집계·승인 보고서를 추가했다. 원본은 수정하지 않았고 staging은 Git에서 제외했다.
  - 검증: 24,000행 수량, 고유 ID·message hash 각 24,000, 영문 단어 잔여 0, AI Hub 축 간 group 겹침 0, BaZi 1,500개 완전 group, YEJI 51규칙·1,200 고유 명식과 단위 테스트를 통과했다. BaZi/YEJI 검수 300건은 사용자 지시로 `owner_risk_accepted` 처리했다.
  - 남은 이슈·후속 작업: 정확한 학습용 MIX20K, holdout/core eval, tokenizer 길이·assistant mask·모델 preflight는 Phase 4에서 생성·검증한다. 현재 `training_promotion_allowed=false`다.
- 2026-08-28
  - 작업 요약: audit v1.0·v1.1·승인 v1.2와 승인 24K staging을 각 build의 당시 Git commit·fingerprint·artifact chain으로 재검증했다. 현재 코드로 승인 staging HTML을 read-only로 다시 실행할 수 있게 했다.
  - 변경 범위: audit v1.2의 correction fingerprint·가변 큐 수량 검증을 보강하고, staging 구현 commit·approval·acceptance·decision·private/public manifest hash를 registry에 pin했다. 당시 산출물은 수정하지 않았다.
  - 검증: 24,000행·고유 ID/message hash 24,000·축별 13,200/6,000/2,400/1,200/1,200·BaZi 1,500 완전 group·AI Hub 축 교집 0·검수 decision 300건·private 0600 권한을 재확인했다. loopback HTML은 HTTP 200, CSP/no-store, read-only, 항목 300개를 확인했다.
  - 남은 이슈·후속 작업: 축 간 동일 명식 leakage group 36개를 Phase 4에서 group-first로 함께 배치한다. 현재 승인은 전문 항목별 품질 인증이 아니며 `training_promotion_allowed=false`다.
- 2026-08-28
  - 작업 요약: 생성기 후속 검수 소견을 반영해 기존 `v0.1.0`을 보존한 채 staging `v0.2.0` 생성·검증 계약을 구현했다.
  - 변경 범위: YEJI 명식을 `lunar-python==1.4.8` 고정 sdist에서 결정론적 유효 달력 명식으로 생성하고 오호둔·오서둔을 이중 검증한다. AI Hub turn projection provenance, 겹침 가능한 정책 필터 집계, Nemotron 나이 `19~99` fail-closed 검증과 승인 hash chain을 추가했다.
  - 검증: Ruff·Python compile·전처리 단위 테스트 12건과 YEJI 1,200건 생성 검사를 통과했다. 생성 명식은 고유 1,200건·역법 관계 유효 1,200건이고 최대 탐색 65회, 평균 3.374167회다.
  - 남은 이슈·후속 작업: 구현 commit을 고정한 뒤 새 24K build를 생성·자동 검증하고 사용자 지시에 따른 위험 수용을 별도 기록한다. 그 결과를 부모로 Phase 4A~E를 다시 실행하기 전까지 기존 승격 상태는 바꾸지 않는다.
