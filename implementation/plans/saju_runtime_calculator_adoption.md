<!-- saju_runtime_calculator_adoption.md - 한국 만세력 계산 runtime의 단일 정본·Gate·데이터 이관 순서를 기록한다. -->

# 한국식 만세력 Runtime 계산기 도입 정본

| 항목 | 값 |
|---|---|
| 문서 버전 | `runtime-calculator-adoption-v2.0.0` |
| 정본화 기준일 | 2026-08-31 |
| 기준 `master` | `8eaa7b6e27d25df35fbd58e0af9ceaea57137649` |
| 기준 모델 run | `KI20-MIX-v2/run-1f5d732cae67` |
| 모델 run 상태 | `trained_and_reloaded`, production 승격 금지 |
| runtime profile | `KR_CIVIL_MIDNIGHT_V1` |
| runtime 상태 | 후보 구현 완료, 공식 전수 Gate 차단 |
| 데이터 상태 | v3.0.1 읽기 전용 분석 완료, v3.1 재생성 차단 |

이 문서는 저장소 루트의 조사 초안 `SAJU_RUNTIME_CALCULATOR_ADOPTION_PLAN.md`를 대체하는 실행 정본이다. 기존 데이터 보정 정본인 [`mix20k_v3_repair_plan.md`](mix20k_v3_repair_plan.md)와 역할을 나눈다.

- v3 repair 정본: LLM tool/state trajectory와 20K 데이터 계약
- 이 정본: tool이 반환할 원국·기간 계산 사실의 권위, 구현, 검증, 앱 연결

## 1. 결론

첫 runtime은 다음처럼 고정한다.

- production 언어는 Python이다.
- 지원 지역은 `country_code=KR`, timezone `Asia/Seoul`뿐이다.
- 지원 양력 연도는 1900~2049다.
- 양력·음력, 정확한 시각·범위·생시 미상을 지원한다.
- 오전/오후는 새 tool enum을 추가하지 않고 `range`로 정규화한다.
- 연 경계는 입춘 순간, 월 경계는 12절 순간, 일 경계는 민간시 00:00, 시주는 민간시를 쓴다.
- 진태양시·균시차는 적용하지 않는다.
- 지지 십신은 지장간 본기를 기준으로 한다.
- 외부 `manseryeok@2.0.0`은 개발 비교기로만 사용하며 Node runtime 의존성으로 넣지 않는다.
- 신강약·격국·용신·대운·공망·12운성·합충형파해·자동 해석은 v1 fact payload에서 제외한다.

계산 코드는 구현됐지만 `runtime_approved=false`다. 현재 결과는 명시적인 candidate 플래그에서만 `HARD_CANDIDATE`로 나오며, 기본 실행은 `RUNTIME_GATE_PENDING`으로 차단한다. 공식 전수 자료가 없는 상태에서 이 플래그를 true로 바꾸거나 v3.1을 생성하지 않는다.

## 2. 불변 보존 범위

다음 산출물은 수정하거나 재해석하지 않는다.

- 기존 `saju-tools-v1`, `saju-session-state-v1`
- `configs/saju_calculation_policy-v1.0.0.json`
- MIX20K-v2와 `mix20k-v3.0.1-repaired/build-94eb7b543490`
- KI10·KI20 checkpoint, manifest, training summary
- 기존 split membership과 sealed blind

runtime 입력·정책·source version·코드 fingerprint가 바뀌면 새 contract/report/build ID를 만든다. 과거 파일을 덮어쓰지 않는다.

## 3. 현재 확인된 입력과 데이터

v3.0.1 private training projection 20,000행을 읽기 전용 재검사한 결과다.

| 항목 | 수량 |
|---|---:|
| `calculate_saju_chart` | 4,350 |
| `calculate_saju_period` | 900 |
| 양력 chart | 3,386 |
| 음력 chart | 964 |
| exact | 3,417 |
| unknown | 933 |
| 출생 연도 | 1960~2005 |
| KR / `Asia/Seoul` | 4,150 |
| 해외 timezone | 200 |
| `HARD_CANDIDATE` 행 | 3,800 |

해외 200행은 한국 전용 제품 범위와 충돌한다. v3.1 생성 시 180행은 같은 축의 한국 사례로 새로 만들고, 20행은 `UNSUPPORTED_REGION` fail-closed trajectory로 바꾼다. 전체 20K는 유지한다.

## 4. 권위와 의존성

| 원천 | 고정값 | 역할 |
|---|---|---|
| KASI 음양력 OpenAPI | [공식 페이지](https://www.data.go.kr/data/15012679/openapi.do) | 양↔음력, 윤달, 일진 공식 conformance |
| KASI 역서 달력자료 | [공식 페이지](https://astro.kasi.re.kr/life/post/calendardata) | 24절기 날짜·시·분 공식 경계 자료 후보 |
| IANA tzdb | `2026c` | 역사 civil time, DST fold/gap |
| Python `tzdata` | `2026.3`, wheel SHA-256 `dc096730…e54931` | 재현 가능한 timezone 배포본 |
| `korean-lunar-calendar` | `0.4.0`, wheel SHA-256 `c042e20d…fe4e7` | KASI 전수 대조 전 음양력 후보 provider |
| Astronomy Engine | `2.1.19@61dc07020aaa6885d2c7f688a4d82beaf6edb9ef`, wheel SHA-256 `232ba7dd…6f67f` | KASI 경계 전수 대조 전 절입 후보 provider |
| `manseryeok` | `2.0.0@fba3253d7305b8b61189bd78318a7a27ed8c9b09` | 개발·비교 전용, production dependency 아님 |

패키지와 source registry는 [`requirements-runtime-calculator.txt`](../../requirements-runtime-calculator.txt)와 [`source_registry-v1.0.0.json`](../../configs/runtime/calculation/source_registry-v1.0.0.json)에 URL·SHA-256까지 고정했다. Astronomy Engine·한국 음양력 라이브러리의 MIT 고지와 `tzdata`의 Apache-2.0 고지는 [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)에 보존한다.

외부 패키지가 결과를 냈다는 사실은 승인 근거가 아니다. 공식·독립 fixture와 프로젝트 profile을 통과한 필드만 승인할 수 있다.

## 5. 구조

```text
saju-tools-v1
  → 입력 검증·한국 범위 정규화
  → IANA Asia/Seoul fold/gap 판정
  → 한국 양음력 후보 provider
  → Astronomy Engine 절입 순간
  → KR_CIVIL_MIDNIGHT_V1 4주 core
  → 오행·지장간·십신 파생
  → exact chart 또는 range/unknown chart set
  → 내부 trace와 LLM-visible allowlist 분리
```

production API 서버를 새로 만들지 않는다. 초기 앱 연결은 Python process 안의 `SajuRuntimeEngine`을 호출한다. 영속 cache도 만들지 않으며 `chart_id`는 동일 process의 기간 계산에만 사용한다.

## 6. 입력 계약

기존 tool enum은 `exact`, `range`, `unknown`을 유지한다.

| 사용자 표현 | 내부 표현 | 금지 사항 |
|---|---|---|
| 정확한 시각 | `exact` + `HH:MM` | 존재하지 않는 DST 시각 이동 |
| 오전 | `range 00:00~11:59` | 09:00 대표값 대입 |
| 오후 | `range 12:00~23:59` | 15:00 대표값 대입 |
| 대략/범위 | 명시 `range` | 범위 중 첫 시각 선택 |
| 모름 | `unknown` | 00:00·12:00 자동 대입 |

Korea-only v1에서 해외 country/timezone은 `UNSUPPORTED_REGION`, 1900~2049 밖은 `UNSUPPORTED_YEAR`다. 자정을 넘는 하나의 시간 범위는 날짜가 불명확하므로 둘로 나눠 확인하게 한다.

DST gap은 `NONEXISTENT_LOCAL_TIME`으로 차단하고 이동하지 않는다. fold는 두 UTC instant를 유지하며 결과 사실이 우연히 같아도 단일 `chart_id`를 발급하지 않는다.

## 7. 결과와 사실 등급

LLM-visible 필드는 기존 allowlist를 유지한다.

```text
status, hard_facts, fact_authority, code, message, limitations
```

다음은 executor 내부에서만 보관한다.

```text
normalized_input, source_versions, warnings,
alternative_charts, chart_id, chart_set_id,
calculation_run_id, internal_trace
```

현재 candidate 결과는 항상 `status=partial`, `fact_authority=HARD_CANDIDATE`다. Runtime Gate 통과 전 `PROFILE_DETERMINISTIC`이나 `ok`로 승격하지 않는다.

ID는 Unicode NFC, 정렬 key, compact UTF-8 JSON과 SHA-256으로 만든다. 다음이 달라지면 ID도 달라진다.

- 정규화 출생 입력
- `policy_id`
- engine·schema version
- tzdb·음양력·절입·표 source version

## 8. v1 계산 범위

### 포함

- 양력↔한국 음력 정규화와 윤달
- 연주·월주·일주
- exact일 때 시주
- 생시 범위·미상의 후보 chart와 공통 `stable_facts`
- 천간·지지, 음양·오행
- 표면 오행 분포
- 지장간
- 천간 십신
- 지장간 본기 기준 지지 십신
- 기간 시작·끝의 연·월·일 간지와 구간 내 절입 목록

### 제외

- 대운·세운 해석
- 공망·12운성
- 합·충·형·파·해
- 신강약 점수
- 격국·용신
- 관계 우선순위
- 자동 통변·미래 사건·색상·방향·소품 조언

기간 tool은 날짜·간지만 반환한다. 원국과의 길흉 관계나 사건을 생성하지 않는다.

## 9. Runtime Gate

모든 조건이 동시에 참이어야 한다.

| Gate | 기준 |
|---|---:|
| KASI 지원 범위 | 1900-01-01~2049-12-31, 54,787일 |
| 12절 경계 | 12절 × 150년 × 전/경계/후 = 5,400 |
| unknown/range | 500 이상 |
| canonical hash | 200 이상 |
| 해외 unsupported | 20 이상 |
| 공식 hard mismatch | 0 |
| profile boundary mismatch | 0 |
| unknown 시주 추측 | 0 |
| DST gap 자동 이동 | 0 |
| DST fold 자동 선택 | 0 |
| host TZ·locale byte drift | 0 |
| heuristic fact leak | 0 |
| 미분류 mismatch | 0 |
| silent fallback | 0 |
| source/profile version ID 불변 오류 | 0 |

KASI service key 또는 공식 전체 snapshot이 없으면 외부 라이브러리 값으로 빈 자리를 채우지 않는다. 수집기는 resume·최대 10,000요청/run·명시 확인·환경변수 key 방식으로 구현했고, redirect를 거부하며 collector version·코드 SHA-256을 snapshot manifest에 묶는다. 현재 network 수집은 수행하지 않았다.

## 10. 현재 검증 결과

공개 보고서:

```text
data/reports/saju_runtime_conformance/v1.0.0/build-8db2f43d91ca/
data/reports/saju_runtime_migration/v1.0.0/build-94eb7b543490/analysis.json
```

| 검사 | 결과 |
|---|---:|
| 공개 KASI 중 지원 범위 | 63건 |
| 음양력 mismatch | 0 |
| 일진 mismatch | 0 |
| 단일 profile 비교 | 16/16 통과 |
| unknown/range | 500/500 |
| canonical ID | 200/200 |
| 해외 차단 | 20/20 |
| host TZ·locale drift | 0 |
| heuristic leak | 0 |
| DST gap 이동·fold 자동 선택 | 0 |

현재 실패는 구현 mismatch가 아니라 공식 fixture 수량 부족이다.

- KASI 전수: `63 / 54,787`
- 12절 경계: `2 / 5,400`
- `runtime_gate_passed=false`
- `mix20k_v3_1_regeneration_allowed=false`
- `training_promotion_allowed=false`

## 11. 실행 순서와 상태

| 순서 | 작업 | 상태 |
|---:|---|---|
| R0 | KI20·v3.0.1·sealed blind 상태 동결 | 완료 |
| R1 | input/output/profile/source/ID/Gate 계약 고정 | 완료 |
| R2 | Python 음양력·절입·4주·불확실성·기간 core 구현 | 완료(후보) |
| R3 | 기존 tool allowlist in-process bridge | 완료(기본 off) |
| R4 | KASI 전수·12절 경계 공식 snapshot 수집 | 차단(자격 증명·snapshot 없음) |
| R5 | full conformance와 profile ADR 승인 | 대기 |
| R6 | v3.1 5,250 tool call 전수 재생성·새 split/preflight | 대기 |
| R7 | 대시보드 `KI20 + Runtime` local lane·앱 canary | 대기 |
| R8 | 새 모델 학습 handoff | 이 계획 범위 밖 |

R4 전에도 candidate CLI와 테스트는 실행할 수 있지만 사용자-facing production 결과나 학습 Gold로 사용하지 않는다.

## 12. MIX20K-v3.1 계약

Runtime Gate 통과 뒤 3,800 `HARD_CANDIDATE`만 고치는 방식은 금지한다. 계산 근거가 포함된 tool payload 전체를 같은 runtime version으로 맞추기 위해 chart 4,350회와 period 900회를 모두 재생성한다.

```text
mix20k-v3.0.1-repaired/build-94eb7b543490 (불변)
  → Runtime Gate 통과
  → 해외 180행 한국 사례 교체 + 20행 UNSUPPORTED_REGION
  → chart 4,350 + period 900 전수 재실행
  → grounded assistant 문장 동시 재생성
  → mix20k-v3.1-runtime-grounded 새 build
  → leakage split·tokenizer·tool round-trip·Phase 4 preflight 재실행
```

Runtime Gate만으로 학습을 허용하지 않는다. 기존에 남은 대화 다양성, 4K/expert 1.5K 검수, state/grounding 품질 Gate도 별도로 통과해야 한다.

## 13. 실행 명령

전용 CPU 환경은 학습용 PyTorch 환경과 분리한다.

```bash
uv venv .venv-runtime
uv pip install --python .venv-runtime/bin/python -r requirements-runtime-calculator.txt

.venv-runtime/bin/python -m scripts.runtime.saju_runtime verify-contract
.venv-runtime/bin/python -m scripts.runtime.saju_runtime environment
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector plan
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.conformance run
```

candidate 계산은 승인 전임을 알고 로컬 검증할 때만 명시한다.

```bash
.venv-runtime/bin/python -m scripts.runtime.saju_runtime calculate \
  --input /로컬/비공개/chart-arguments.json \
  --enable-candidate-runtime
```

공식 수집은 service key를 환경변수로 제공하고 정확한 확인 문자열을 넣어야 한다. key는 manifest·stdout·URL 로그에 기록하지 않는다.

```bash
KASI_LUNISOLAR_SERVICE_KEY='...' \
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector collect \
  --output data/raw/saju_runtime/kasi_lunisolar/v1 \
  --max-requests 10000 \
  --confirm-network COLLECT_KASI_OFFICIAL_SNAPSHOT
```

## 14. 완료 기준

- [x] 단일 Korea-only profile과 지원 범위를 고정했다.
- [x] 버전·URL·wheel SHA-256·MIT 고지를 고정했다.
- [x] 기본 off와 explicit candidate mode를 분리했다.
- [x] 음양력·절입·4주·오행·지장간·십신·기간 core를 구현했다.
- [x] unknown/range와 DST fold/gap을 추측 없이 처리한다.
- [x] LLM-visible allowlist에서 내부 trace·ID를 숨긴다.
- [x] KASI 수집기와 conformance report를 구현했다.
- [x] v3.0.1 20K를 읽기 전용 분석하고 v3.1 이관 수량을 고정했다.
- [ ] KASI 54,787일 공식 snapshot을 확보한다.
- [ ] 12절 5,400 경계 fixture를 확보한다.
- [ ] Runtime Gate를 통과하고 profile ADR을 승인한다.
- [ ] v3.1을 새 fingerprint로 생성하고 split/preflight를 재실행한다.
- [ ] feature flag 기본 off 상태로 앱·대시보드 canary를 검증한다.

## 진행 기록

- 2026-08-31
  - 작업 요약: 루트 조사 초안을 현재 KI20·MIX20K-v3.0.1 상태에 맞는 Korea-only 실행 정본으로 축소·재작성하고, versioned 계약과 Python candidate runtime을 구현했다.
  - 변경 범위: `configs/runtime/calculation`, `scripts/runtime/calculation`, KASI 수집기, conformance v2, v3.1 읽기 전용 이관 분석기, 전용 requirements와 제3자 고지를 추가했다. 기존 tool/session schema, 데이터 build, checkpoint, sealed blind는 변경하지 않았다.
  - 검증: `uvx ruff check scripts tests`, runtime·conformance·v3 이관 unit 26건, JSON/manifest/hash chain 검증을 통과했다. 공개 KASI 지원 범위 63건의 음양력·일진 mismatch 0, 단일 profile 16/16, unknown/range 500, hash 200, 해외 20, host TZ·locale·DST·heuristic leak 검사도 통과했다. 최종 구현 hash를 반영한 보고서 `build-8db2f43d91ca`는 공식 전수 수량 부족으로 `runtime_gate_passed=false`다.
  - 남은 이슈·후속 작업: KASI service key 또는 검증된 공식 전체 snapshot과 12절 경계 자료가 필요하다. 확보 전 v3.1 생성, 앱 기본 활성화, 실제 학습을 수행하지 않는다.
