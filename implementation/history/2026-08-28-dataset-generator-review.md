<!-- 2026-08-28-dataset-generator-review.md - MIX20K staging 24K 생성 어댑터 코드 검수 기록 -->

# staging 24K 생성 어댑터 검수 기록

## 진행 기록

- 날짜: 2026-08-28
- 작업 요약: 승인된 staging build `v0.1.0/build-109815ee6879`의 24,000행을 생성한 어댑터 코드를 검수하고, 산출물 표본이 아닌 생성 경로에서 확인된 6건을 기록했다. 코드·설정·산출물 변경은 없다.
- 검수 범위: `scripts/data/preprocess_adapters.py`, `scripts/data/preprocess_tools.py`, `scripts/data/phase2b_verify_history.py`, `scripts/data/audit_tools.py`의 명식 처리 경로와 `configs/data_versions/saju_1b_baseline/audit-policy-v1.2.0.json`의 `safety_patterns`.
- 검수 동기: `data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/gate.accepted.json`의 `domain_item_review_performed`가 `false`이고 `content_review_method`가 `owner_blanket_risk_acceptance`다. 항목 내용 검수가 수행되지 않은 상태이므로, 표본 300건 대신 24,000행 전체를 결정한 생성 로직을 검수 대상으로 삼았다.
- 검수 한계: 이 검수는 **원문 품질 검수가 아니다**. `data/raw`, `data/staging`가 Git 제외 경로라 작업 환경에 원문이 없고, 공개 원천 재수집도 egress 정책상 `huggingface.co`가 차단되어 불가능했다. 번역투, 사실 오류, 톤 일관성, 서사 완성도는 확인하지 못했으며 이 영역은 여전히 미검수 상태다.
- 검증 명령/결과:
  - 인용한 모든 파일 경로와 행 번호를 `sed -n`으로 대조해 실제 코드와 일치함을 확인했다.
  - 소견 1의 정량 근거는 아래 재현 절차로 산출했다.
  - `git diff --check`: 공백 오류 없이 통과했다.
- 남은 이슈/후속 작업: 소견 1은 승인된 build의 입력 계약에 해당하므로, 수정 시 기존 build를 덮지 않고 새 version으로 분기해야 한다. 소견 4는 안전 정책 판단이 필요해 소유자 결정 대상으로 남긴다. 조치 여부와 무관하게 이 기록은 `training_promotion_allowed` 판정 전에 함께 검토한다.

## 후속 독립 재검증과 처분

같은 날 원천과 Git 제외 staging이 있는 실행 환경에서 위 소견을 다시 측정했다. 아래 결과는 최초 검수 당시의 한계를 지우지 않고 후속 근거로 추가한다.

| 소견 | 후속 확인 | 처분 |
|---|---|---|
| 1 | 기존 YEJI 1,200행 중 역법 관계 유효 49행, 무효 1,151행이다. 기존 chart에 대한 evaluator/meta 불일치는 0건이지만 월간·시간을 오호둔·오서둔으로 치환하면 evaluator 결과가 56건 바뀐다. | 높음 유지. 문자열 두 개만 고치지 않고 유효 달력 명식을 새로 생성한 뒤 목표 polarity를 다시 평가한다. |
| 2 | 새 분리 카운터에서 Nemotron 정책 합집합은 238,015행, 영문 일치는 49,248행이다. AI Hub 정책 합집합은 4,500행이다. | 수용·수정. 합집합, 주 제외 사유, 겹침 가능한 일치 사유를 따로 기록한다. |
| 3 | 영문 일치 규모는 측정했지만 49,248행은 prompt·서사 전체 기준이므로 직업 필드 기여분은 여전히 미측정이다. | 근거 한계를 명시한다. 영문 전면 금지 계약은 이번 baseline에서 유지한다. |
| 4 | AI Hub에서 임상 2,487회, 추가 의료 1,708회가 일치했다. 서로 겹칠 수 있으므로 합계는 제외 행 수가 아니다. | 공감 표현 coverage 한계로 기록하되 이번 baseline의 보수적 제외 정책은 유지한다. |
| 5 | 원천 58,268건은 모두 2쌍 10,891건 또는 3쌍 47,377건이다. 1쌍 원천이 없어 `pair_count < 2`가 정상 단일턴을 버린다는 영향은 관측되지 않았다. | 낮음으로 하향. 오류가 아니라 첫 1쌍/첫 2쌍 projection 계약을 명시할 문제로 정정한다. |
| 6 | Nemotron 1,000,000행에서 나이 `19~99` 밖 또는 비정수는 0건이다. | 잠재 입력 검증 결함으로 유지하되 실제 데이터 영향은 0건. 새 코드는 fail-closed로 검증한다. |

소견 1의 보강 주장 중 `year_stem, _month_stem, day_stem, _ = stems`만으로 “월간·시간 교정은 라벨에 무영향”이라고 결론 내릴 수는 없다. evaluator의 `_token_present`는 네 천간·네 지지를 모두 조회하고, `valid_pillars`는 시주까지, 규칙 10·11은 전체 간지 네 개를 조회한다. 실제 56건 변화는 규칙 3·4·5·6·7·10·11·18·22·47에서 발생했다. 따라서 새 `v0.2.0`은 고정 달력 anchor에서 명식을 생성하고 그 명식으로 evaluator 목표를 다시 만족시키는 방식을 사용한다.

후속 검증 대상은 `v0.2.0/build-847088ee804d`이며 24,000행 전수 검사, YEJI 역법 관계 1,200/1,200, Nemotron·BaZi 역법 관계 13,200/13,200·6,000/6,000을 통과했다. 승인 방식은 자동 검증 결과에 대한 사용자 위험 수용이고 `domain_item_review_performed=false`, `quality_certification_claimed=false`, `training_promotion_allowed=false`를 유지한다.

## 소견 1 (높음) - YEJI 축 명식의 역법 정합성 미검증

`scripts/data/preprocess_adapters.py:1185`의 `_random_unique_chart`는 네 기둥을 60갑자에서 각각 독립 추출한다.

```python
chart = tuple(rng.choice(JIAZI) for _ in range(4))
```

실제 명식에서 네 기둥은 독립이 아니다. 월주 천간은 년간에서 오호둔(五虎遁)으로, 시주 천간은 일간에서 오서둔(五鼠遁)으로 결정된다. 따라서 년주가 정해지면 가능한 월주는 60개 중 12개, 일주가 정해지면 가능한 시주도 12개다.

같은 생성 방식으로 20만 표본을 검사한 결과는 다음과 같다.

| 항목 | 측정값 | 이론값 |
|---|---:|---:|
| 월주 오호둔 정합 | 20.04% | 1/5 |
| 시주 오서둔 정합 | 20.03% | 1/5 |
| 네 기둥 모두 정합 | 3.94% | 1/25 |

`yeji_shensha_derived` 축 1,200행 기준 실재 가능한 명식은 약 47행, 불가능한 명식은 약 1,153행으로 추정된다. 신살 판정 라벨 자체는 evaluator가 규칙대로 산출하므로 규칙 기준으로는 정합하지만, 입력 명식이 역법상 성립하지 않는다.

정본 계약은 "모델은 생년월일에서 사주 원국을 계산하지 않는다. 런타임 계산기가 제공한 구조화 명식을 근거로 해석한다"로 고정되어 있다. 런타임 계산기는 유효 명식만 산출하므로, 이 축에서 학습 분포와 서빙 분포가 어긋난다.

파이프라인에 이를 검출하는 코드는 없다. `scripts/data/audit_tools.py:534`와 `:710`의 `invalid_chart`는 Nemotron과 `bazi_sft`에만 적용되고, 그 판정도 `scripts/data/audit_tools.py:385`에서 보듯 간지 토큰이 60갑자 표에 있는지 확인하는 형식 검사다. 외부 원천 명식은 검사하고 자체 생성 명식은 검사하지 않는 비대칭이 있다.

표본 300건 육안 검수로는 검출되기 어렵다. 검수자가 각 명식마다 오호둔·오서둔을 수기로 대조해야 한다.

### 재현 절차

```python
import random

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
JIAZI = tuple(STEMS[i % 10] + BRANCHES[i % 12] for i in range(60))

def month_ok(year, month):                       # 五虎遁
    n = (BRANCHES.index(month[1]) - 2) % 12      # 0 = 寅월
    return STEMS.index(month[0]) == ((STEMS.index(year[0]) % 5) * 2 + 2 + n) % 10

def hour_ok(day, hour):                          # 五鼠遁
    n = BRANCHES.index(hour[1])                  # 0 = 子시
    return STEMS.index(hour[0]) == ((STEMS.index(day[0]) % 5) * 2 + n) % 10

rng = random.Random(42)
valid = sum(
    month_ok(c[0], c[1]) and hour_ok(c[2], c[3])
    for c in (tuple(rng.choice(JIAZI) for _ in range(4)) for _ in range(200_000))
)
print(valid / 200_000)
```

## 소견 2 (높음) - `excluded_safety` 카운터가 두 종류의 배제를 합산

`scripts/data/preprocess_adapters.py:364`~`:368`은 안전 패턴 일치와 영문 단어 잔여를 같은 카운터로 집계한다.

```python
if (
    _contains_any(patterns, rendered_input_values)
    or ASCII_WORD_PATTERN.search(rendered_input_values)
    or ASCII_WORD_PATTERN.search(narrative_text)
):
    counters["excluded_safety"] += 1
```

`ASCII_WORD_PATTERN`은 `[A-Za-z]{2,}`이므로 `IT`, `AI`, `CEO`, `PD`, `SNS` 같은 토큰이 한 번 등장한 행도 같은 카운터로 들어간다. `scripts/data/preprocess_adapters.py:880`~`:883`의 AI Hub 경로도 동일하다.

그 결과 `aggregate.json`의 `nemotron_saju.filter_counts.excluded_safety`(238,015, 원천 100만 행의 23.8%)와 `aihub_empathy.filter_counts.excluded_safety`(4,500)는 안전성 지표로 해석할 수 없다. 두 원인이 분해되지 않아 안전 패턴이 실제로 무엇을 얼마나 배제했는지 측정이 불가능하다.

두 카운터를 분리하면 기존 build의 산출물을 바꾸지 않고도 다음 build부터 관측 가능해진다.

## 소견 3 (중상) - 영문 전면 금지의 페르소나 분포 영향이 미측정

영문 단어 금지는 의도된 계약이다. `scripts/data/preprocess_tools.py:363`과 `scripts/data/phase2b_verify_history.py:323`이 최종 학습 messages에 영문 단어가 남으면 예외를 던진다. 설계 결정으로 인정한다.

다만 Nemotron은 현대 한국인 페르소나 데이터셋이므로 직업·경력 필드의 영문 표기가 분포에 영향을 줄 가능성이 있다. 최초 검수에서는 원천을 보지 못했으므로 특정 직군이 흔하다는 서술은 추론이었다. 후속 전수 검사에서 prompt·서사 전체의 영문 일치는 49,248행으로 측정됐지만, 그중 직업 필드가 원인인 행 수는 별도 필드 카운터가 없어 여전히 미측정이다.

소견 2의 카운터 분리가 선행되면 이 영향의 크기를 판단할 수 있다.

## 소견 4 (중상) - 공감 코퍼스에서 통증·우울 표현이 unsafe로 배제

`scripts/data/preprocess_adapters.py`의 `ADDITIONAL_UNSAFE_PATTERNS`에 `아프(?:다|고|다는|다는 것을)`, `증상`, `통증`, `복용`, `환자`가 있고, `configs/data_versions/saju_1b_baseline/audit-policy-v1.2.0.json`의 `aihub_clinical`에 `우울증`, `진단`, `치료`, `약물`이 단어 경계 없이 들어 있다.

AI Hub #86은 감성 대화 말뭉치다. 이 패턴 집합을 적용하면 "마음이 아프다" 계열 표현이 의료 발화로 분류되어 배제된다. 감정 상담 맥락에서 흔한 표현이다.

`aihub_self_harm` 계열 배제는 타당하다. 다만 `우울증`, `치료`, `아프다`를 같은 등급으로 묶은 부분은 재검토 대상으로 본다. 해당 발화를 학습에서 제거해도 추론 시점에 사용자가 그 표현을 사용하지 않게 되는 것은 아니며, 모델에 해당 상황의 학습된 응답이 없어진다. 위기 대응은 일반적으로 학습 데이터 제거가 아니라 런타임 가드레일로 다룬다.

이 항목은 안전 정책 판단이므로 코드 소견이 아닌 소유자 결정 사항으로 남긴다.

후속 처리에서는 이 패턴을 완화하지 않았다. 따라서 위기·임상 위험을 보수적으로 줄이는 대신 “마음이 아프다” 같은 비임상 공감 표현의 coverage도 함께 줄 수 있다는 한계를 데이터 카드와 Phase 4 해석에 남겨야 한다. 런타임 가드레일 필요성도 이 데이터 제외만으로 충족됐다고 간주하지 않는다.

## 소견 5 (낮음) - 단일턴·멀티턴 활용형이 원천 turn 수와 같은 뜻은 아님

`scripts/data/preprocess_adapters.py:874`의 `pair_count < 2` 게이트가 단일턴·멀티턴 두 축 모두에 적용된다. 따라서 HS/SS 쌍이 1개인 대화는 단일턴 축에서도 `excluded_structure`로 전량 제외되고, `aihub_empathy_single`은 2턴 이상 대화의 첫 턴만 사용한 결과가 된다.

두 축의 구분은 데이터 속성이 아니라 `ordered_groups[:single_target]`과 `[single_target:required]`라는 rank 위치다. 후속 전수 검사에서 원천은 모두 2쌍 또는 3쌍이어서 1쌍짜리 정상 대화가 이 조건 때문에 제외된 사례는 없었다. 따라서 심각도를 낮음으로 정정하며, `aihub_empathy_single`은 “원천의 첫 1쌍 projection”, 멀티턴 축은 “첫 2쌍 projection”이라는 활용형을 provenance에 명시한다. 그룹 비중첩 계약은 그대로 충족한다.

## 소견 6 (중하) - 결측 나이가 `0대`로 렌더링

`scripts/data/preprocess_adapters.py:336`은 다음과 같다.

```python
"age_band": f"{max(0, int(age or 0) // 10) * 10}대",
```

`age`가 `None`이거나 `0`이면 `0대`가 생성되고, `scripts/data/preprocess_adapters.py:414`를 통해 user 메시지에 그대로 들어간다. 또한 이 줄은 상위 `try/except` 블록 밖이므로 `age`가 비숫자 문자열이면 예외가 잡히지 않고 빌드가 중단된다. 승인된 build가 성공했으므로 실제 원천에서는 숫자였을 것으로 보이나, 결측 여부는 확인하지 못했다.

## 검수에서 문제가 확인되지 않은 부분

- `bazi_sft` 구조 검산은 견고하다. `scripts/data/preprocess_adapters.py:523`~`:554`의 `_validate_bazi_facts`가 여덟 글자에서 `element_counts`를 재계산해 원천과 대조하고, 일간이 일주 천간·오행과 일치하는지 확인하며 불일치 시 예외를 던진다.
- 전반적으로 fail-closed다. 축별 목표 미달, 축 간 group 중첩, exact duplicate, 규칙 수 불일치에서 모두 예외를 던진다.
- 선별 결정론성이 유지된다. `stable_rank`가 seed와 식별자만으로 순위를 정하고, 후보 heap과 최종 정렬이 모두 rank 기준이다.
- leakage group이 명식 서명 기준으로 부여되어 원천이 달라도 동일 명식이 하나의 group으로 묶인다.
