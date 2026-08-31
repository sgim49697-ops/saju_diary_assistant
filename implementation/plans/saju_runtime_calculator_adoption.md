<!-- saju_runtime_calculator_adoption.md - 한국 만세력 계산 runtime의 단일 정본·Gate·데이터 이관 순서를 기록한다. -->

# 한국식 만세력 Runtime 계산기 도입 정본

| 항목 | 값 |
|---|---|
| 문서 버전 | `runtime-calculator-adoption-v2.3.0` |
| 정본화 기준일 | 2026-08-31 |
| 구현 시작 기준 `master` | `22fa6943c625b7caad7a7fb9cfd174a6a01992e6` |
| 기준 모델 run | `KI20-MIX-v2/run-1f5d732cae67` |
| 모델 run 상태 | `trained_and_reloaded`, production 승격 금지 |
| runtime profile | `KR_CIVIL_MIDNIGHT_V1` |
| runtime 상태 | v1.2 천문·HMAC·session v2.1·FSM v1.1 보강 완료, 공식 snapshot·표시 분 Gate 차단 |
| 데이터 상태 | v3.1 생성·비학습 preflight 구현, release 전 실행 차단 |

이 문서는 앞서 제공된 `SAJU_RUNTIME_CALCULATOR_ADOPTION_PLAN.md` 조사 초안을 대체하는 저장소 실행 정본이다. 기존 데이터 보정 정본인 [`mix20k_v3_repair_plan.md`](mix20k_v3_repair_plan.md)와 역할을 나눈다.

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

기존 v1 candidate와 v1.1 산출물은 보존하고, v1.2 승인 wrapper·동적 release registry·HMAC ID·구조화 intake FSM을 새 버전으로 구현했다. R4~R5 실행으로 음양력 54,787일과 1964년 역서 근거는 확보했지만 24절기 OpenAPI의 실제 반환 범위가 2000~2028년뿐이고 두 provider 중 전체 적격 후보도 없다. 따라서 현재 `runtime_approved=false`이며 release registry도 생성하지 않았다. v1.2는 통과 보고서와 구현·계약·공식 snapshot 해시가 모두 일치하고 production HMAC key가 준비된 경우에만 exact 결과를 `HARD_GT`, range/unknown의 공통 사실을 `POLICY_BOUND_RULE`로 승격한다. 그 전에는 `RUNTIME_RELEASE_REQUIRED`로 차단하며 v3.1 생성과 앱 canary를 진행하지 않는다.

## 2. 불변 보존 범위

다음 산출물은 수정하거나 재해석하지 않는다.

- 기존 `saju-tools-v1`, `saju-session-state-v1`(학습·과거 세션 계약).
- `session_state_schema_v2.json`·FSM/Gate v1.0은 과거 불변 산출물로 보존한다. session v2의 중복 `period` key 결함을 고치기 위해 앱 후보는 별도 `session_state_schema_v2.1.0.json`·FSM/Gate v1.1·`intake_registry-v1.1.0.json`만 사용한다.
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
| KASI 24절기 OpenAPI | [공식 페이지](https://www.data.go.kr/data/15012690/openapi.do) | 1900~2049 전 연도 제공 여부와 반환된 공식 날짜를 snapshot으로 보존. 2026-08-31 scan에서는 2000~2028년 696건만 반환했으므로 3,600건 전수 oracle로 간주하지 않음 |
| KASI 디지털 역서 | [공식 archive](https://astro.kasi.re.kr/kor/almanac/pageView/26) | 1964년 역서 `KASI_A188_Z_001` 20쪽의 백로 `9월 7일 24시 00분` 원문·이미지와 정규화 근거 |
| KASI 달력자료 | [공식기관 표시 페이지](https://astro.kasi.re.kr/kor/life/post/calendarData) | 2021~2027년 12절 84건의 표시 분 reference. 페이지 고지대로 공식 월력요항 정답으로 승격하지 않음 |
| IANA tzdb | `2026c` | 역사 civil time, DST fold/gap |
| Python `tzdata` | `2026.3`, wheel SHA-256 `dc096730…e54931` | 재현 가능한 timezone 배포본 |
| `korean-lunar-calendar` | `0.4.0`, wheel SHA-256 `c042e20d…fe4e7` | KASI 전수 대조 전 음양력 후보 provider |
| Astronomy Engine | `2.1.19@61dc07020aaa6885d2c7f688a4d82beaf6edb9ef`, wheel SHA-256 `232ba7dd…6f67f` | KASI 경계 전수 대조 전 절입 후보 provider |
| Skyfield / JPL DE440s | `1.55`, DE440s SHA-256 `c1c7feea…0a49f2` | 1900~2049년 12절 1,800건의 독립 provider 후보 비교. conformance v5에서 고정 달력 bracket으로 계산하며 production에는 선택·연결하지 않음 |
| jplephem / NumPy / sgp4 / certifi | `2.24` / `2.2.6` / `2.27` / `2026.7.22` | Skyfield validator의 고정 전이 의존성 |
| `manseryeok` | `2.0.0@fba3253d7305b8b61189bd78318a7a27ed8c9b09` | 개발·비교 전용, production dependency 아님 |

v1.1 패키지와 원천은 그대로 보존한다. v1.2는 [`requirements-runtime-calculator-v1.2.txt`](../../requirements-runtime-calculator-v1.2.txt)와 [`source_registry-v1.2.0.json`](../../configs/runtime/calculation/source_registry-v1.2.0.json)에 같은 wheel·DE440s identity, 새 교차검증 구현 hash, 판정 범위와 근거 문서를 고정했다. v1.3 source registry와 Gate는 실제 API coverage scan·1964년 역서·동등한 provider 후보 비교만 추가하며 runtime release 계약은 만들지 않는다. Astronomy Engine은 공식 설명대로 compact·truncated VSOP87/NOVAS 계열이고 약 ±1 arcminute 설계 목표를 가지며, Skyfield는 여러 time scale과 ΔT를 별도로 관리한다. 이 차이는 80초 격차와 일관되지만 단일 원인으로 확정하지 않는다. 제3자 라이선스와 JPL 비추적 조건은 [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)에 보존한다. DE440s와 KASI 원문·snapshot은 Git에 넣지 않는다.

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
  → conformance v4: 공식 날짜 판정 + 최근접 분 표기 + 비권위 120초 회귀 가드
  → conformance v5: 실제 API coverage + 1964 공식 역서 + Astronomy/Skyfield provider 적격성
  → HMAC-SHA256 v2 출생 파생 ID
  → 구현·계약·공식 snapshot hash 결합 release registry
  → ApprovedSajuRuntimeEngineV12(feature flag 기본 off, production key 필수)
  → 구조화 intake FSM v1.1(app precondition 6개, 자유문 parser 없음, HMAC call 상관관계)
  → 내부 trace와 LLM-visible allowlist 분리
```

production API 서버를 새로 만들지 않는다. 초기 앱 연결은 Python process 안의 `ApprovedSajuRuntimeEngine`을 호출한다. 영속 계산 cache도 만들지 않으며 `chart_id`는 동일 process의 기간 계산에만 사용한다. 대시보드 canary만 로컬 state에 정규화 입력·모델 공개 결과·snapshot hash 이력을 저장하고, server 재시작 시 같은 release로 chart를 재실행해 fingerprint가 같을 때만 기간 계산을 복구한다.

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

기존 candidate 결과는 항상 `status=partial`, `fact_authority=HARD_CANDIDATE`다. v1.2 승인 경로는 release가 없으면 `status=blocked`, `code=RUNTIME_RELEASE_REQUIRED`이며 사실 등급을 내지 않는다. 유효 release·명시적 feature flag·production HMAC key가 모두 있을 때만 exact를 `HARD_GT`, range/unknown 공통 사실을 `POLICY_BOUND_RULE`로 반환한다.

v1·v1.1의 평문 SHA-256 ID는 과거 불변 산출물에만 남긴다. v1.2의 모든 출생 파생 ID(`birth_input_id`, `chart_id`, `chart_set_id`, `calculation_run_id`, `chart_input_fingerprint`)는 Unicode NFC·정렬 key·compact UTF-8 JSON을 domain-separated HMAC-SHA256으로 만든다. 32바이트 key는 현재 사용자 소유 0600 일반 파일에서만 읽고 값·hash를 보고서에 쓰지 않는다. key 교체 시 세션을 무효화하고 재계산하며, 도입 전 runtime 세션이 0건이므로 legacy migration은 하지 않았다. 다음이 달라지면 ID도 달라진다.

- 정규화 출생 입력
- `policy_id`
- engine·schema version
- tzdb·음양력·절입·표 source version

HMAC은 raw 출생 state 암호화가 아니다. 앱 연결에는 저장 암호화와 보존·삭제 정책이 별도로 필요하다.

FSM v1.1의 chart·period action은 calculation-run HMAC domain의 `scr2_` call ID를 별도로 발급한다. app adapter는 이 값을 tool 결과 event에 그대로 결합해야 하며 FSM은 현재 state revision·arguments에서 재계산한 값과 다르면 stale 결과로 거부한다. 저장된 chart의 `sif2_` fingerprint도 현재 slot과 다시 결합해 correction 우회나 cache 변조를 차단한다.

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
| KASI 음양력·일진 | 1900-01-01~2049-12-31, 54,787일, mismatch 0 |
| KASI 24절기 날짜 수집 | 24절기 × 150년 = 3,600, 누락 0 |
| KASI 12절 날짜 비교 | 12절 × 150년 = 1,800, runtime mismatch 0. 엔진 간 날짜 차이는 해당 KASI 행이 없으면 미해결 차단 |
| KASI 표시 분 reference | 2021~2027년 12절 84건, KST 최근접 분·30초 half-up label mismatch 0. signed 초 차이는 진단 전용 |
| Skyfield/JPL 독립 절입 | 12절 × 150년 = 1,800, Astronomy Engine과 고정 120초 회귀 가드 이내, 절기 identity·시간 순서 오류 0. 120초는 정확도 oracle 아님 |
| 내부 profile 전/경계/후 | 12절 × 150년 × 3 = 5,400, 배정 mismatch 0. 경계 순간 정확도 검증으로 표현 금지 |
| unknown/range | 500 이상 |
| HMAC v2 ID | 200 이상, 재현·prefix·domain·key 분리 오류 0 |
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

KASI service key 또는 공식 전체 snapshot이 없으면 외부 라이브러리 값으로 빈 자리를 채우지 않는다. 인증 API 수집기는 월/연 단위 resume·최대 10,000요청/run·명시 확인·0600 key 파일 우선 방식으로 구현했다. 1,800개월과 150년을 합쳐 1,950요청이며 redirect·부분 응답·경로 이탈·변조 재사용을 거부한다. 2026-08-31 실제 수집에서 음양력은 전 범위를 반환했지만 24절기 API는 1900~2049년 150개 요청 중 2000~2028년만 24건씩 반환했다. 새 coverage 수집기는 0건 연도도 response hash와 함께 기록하고 계산 provider로 채우지 않는다.

인증이 필요 없는 달력자료는 2021~2027년 HTML 7개를 실제 수집했다. 84개 절입 분 값은 생성값이 아니라 각 원문을 매번 재파싱한 결과이며 snapshot 행·manifest·원문 SHA-256·collector SHA-256이 모두 맞아야 한다. 페이지의 자체 고지를 존중해 이 계층은 `institutional_minute_display_reference_not_formal_almanac`로만 사용한다. KASI가 반올림 규약을 공개한 것으로 확인하지 못했으므로 최근접 분·30초 half-up은 Skyfield 84/84 일치에서 도출한 프로젝트 등가 규칙으로만 기록한다.

## 10. 현재 검증 결과

공개 보고서:

```text
data/reports/saju_runtime_conformance/v1.2.0/build-08ea29de9e94/
data/reports/saju_runtime_conformance/v1.2.0/build-ec510bc6922d/
data/reports/saju_runtime_conformance/v1.3.0/build-ef1b8ddb527e/
data/reports/saju_runtime_intake_fsm/v1.1.0/build-3366376bb01b/
data/reports/saju_runtime_migration/v1.0.0/build-94eb7b543490/analysis.json
```

`build-08ea29de9e94`와 FSM v1.0 `build-571d0e82ee0e`는 당시 코드의 이력 산출물로 보존한다. 현재 판단은 Gate 집계 재계산·strict JSON·session v2.1/FSM v1.1을 포함한 뒤의 두 새 build를 따른다.

| 검사 | 결과 |
|---|---:|
| KASI 음양력 공식 snapshot | 54,787/54,787일, 양음력·일진 mismatch 0/0 |
| KASI 24절기 API 전 연도 scan | 150/150년 요청, 실제 지원 2000~2028년 |
| KASI 24절기/12절 공식 날짜 | 696/3,600, 348/1,800. 반환 범위에서 두 provider 날짜 mismatch 0 |
| KASI 표시 분 reference | 84/84 |
| Astronomy Engine ↔ KASI 최근접 분 label | 68/84, mismatch 16 |
| Skyfield/JPL ↔ KASI 최근접 분 label | 84/84, mismatch 0 |
| Astronomy Engine signed 차이 범위 | -43.226729~59.457159초, 진단 전용 |
| Skyfield/JPL signed 차이 범위 | -28.827899~29.281515초, 진단 전용 |
| Astronomy Engine ↔ Skyfield/JPL | 1,800/1,800, 120초 초과 0 |
| 독립 비교 평균 절대 차이 / p99 / 최대 | 17.130844초 / 57.566090초 / 80.666231초 |
| ΔT 정렬 뒤 평균 절대 차이 / 최대 | 17.151252초 / 79.772174초, 평균 100.11913% 잔존 |
| 엔진 간 한국 날짜 차이 | 1964년 백로 1건. KASI 역서 `9월 7일 24:00`을 `9월 8일 00:00`으로 정규화하면 Astronomy Engine 일치, Skyfield 불일치 |
| provider 적격성 | Astronomy는 최근접 분 16건 불일치, Skyfield는 1964 공식 civil date 불일치, 둘 다 공식 1,800행 coverage 부족으로 선택 없음 |
| 독립 비교 절기 identity·순서 오류 | 0 / 0 |
| 내부 profile 배정 전/경계/후 | 5,400/5,400, mismatch 0. 순간 정확도 검사는 아님 |
| 단일 profile 비교 | 16/16 통과 |
| unknown/range | 500/500 |
| HMAC v2 ID | 200/200, 재현·prefix·domain·key 분리 오류 0 |
| 구조화 app FSM | 100/100, 계산된 구조·변조 check 18/18 |
| 기존 KI20 모델 handoff | 14/100, 개선 주장 없음 |
| 해외 차단 | 20/20 |
| host TZ·locale drift | 0 |
| heuristic leak | 0 |
| DST gap 이동·fold 자동 선택 | 0 |

독립 엔진끼리 1964년 백로의 한국 날짜가 한 번 다르다. KASI 디지털 역서 원문은 `9월 7일 24시 00분`이며 civil datetime으로 정규화하면 `1964-09-08T00:00+09:00`이다. Astronomy Engine은 이 날짜·표시 분과 일치하고 Skyfield는 `1964-09-07T23:59`로 불일치하므로 v5는 civil date 정책 판정을 Astronomy Engine으로 기록한다. 다만 원문 정밀도가 분이므로 sub-minute 물리 정확도까지 판정한 것으로 표현하지 않는다.

현재 실패에는 공식 fixture 수량 부족뿐 아니라 provider별로 서로 다른 공식 근거 실패가 포함된다. Astronomy Engine은 1964 공식 civil date를 통과하지만 표시 분 16건이 다르고, Skyfield는 표시 분 84건을 통과하지만 1964 공식 civil date가 다르다. 따라서 어느 provider도 선택하지 않으며 provider 수정·교체 또는 물리적으로 정당화된 새 정책이 별도 필요하다.

- KASI 음양력 전수: `54,787 / 54,787`, mismatch 0
- KASI 24절기 API scan: `150 / 150년`, 반환 `696 / 3,600`
- KASI 12절 날짜: `348 / 1,800`, 반환 범위 provider mismatch 0
- KASI 1964년 역서 백로: `9월 7일 24:00`, normalized civil date 판정 완료
- KASI 표시 분 자료 수량: `84 / 84`, 완료
- runtime 최근접 분 label: `68 / 84`, Gate 실패
- 독립 절입 수량·비권위 회귀 가드: `1,800 / 1,800`, 120초 초과 0
- 엔진 간 날짜 판정: `1964년 백로 civil date는 Astronomy Engine`, sub-minute 물리 정확도 미판정
- provider 선택: `없음`, Gate 실패
- 내부 profile 배정 전/경계/후: `5,400 / 5,400`, 배정 mismatch 0
- `runtime_gate_passed=false`
- `runtime_approved=false`
- `release_approval_performed=false`
- `release_registry_creation_allowed=false`
- `mix20k_v3_1_regeneration_allowed=false`
- `training_promotion_allowed=false`

## 11. 실행 순서와 상태

| 순서 | 작업 | 상태 |
|---:|---|---|
| R0 | KI20·v3.0.1·sealed blind 상태 동결 | 완료 |
| R1 | input/output/profile/source/ID/Gate 계약 고정 | v1.2 HMAC·분·날짜 판정 계약까지 완료 |
| R2 | Python 음양력·절입·4주·불확실성·기간 core 구현 | 완료(후보) |
| R3 | 기존 tool allowlist in-process bridge | 완료(기본 off) |
| R4 | KASI 전수·계층형 절입 snapshot 수집 | 부분 완료(음양력 54,787일·표시 분 84건·1964 역서 완료, 24절기 API는 실제 반환 696건으로 전수 Gate 미달) |
| R5 | full conformance와 profile ADR 승인 | v5 provider 비교 완료, 적격 provider 없음·공식 절기 coverage 부족으로 ADR 승인 차단 |
| R6 | v3.1 5,250 tool call 전수 재생성·새 split/preflight | 생성기·preflight 구현 완료, release 전 입력도 읽지 않고 차단 |
| R7 | 대시보드 `KI20 + Runtime` local lane·앱 canary | v1.8은 기존대로 비활성. session v2.1/FSM v1.1 합성 Gate 통과, 실제 adapter·release·key·암호화·보존 정책 전 앱 연결 금지 |
| R8 | 새 모델 학습 handoff | 이 계획 범위 밖 |

R4 완료 전에도 candidate·독립 validator와 대시보드 UI는 검증할 수 있지만 사용자-facing production 결과나 학습 Gold로 사용하지 않는다. 현재 실행 중인 기존 dashboard process는 재시작하지 않았으며 새 UI는 v1.7 backend에서 runtime endpoint가 없으면 패널을 자동으로 숨긴다.

## 12. MIX20K-v3.1 계약

Runtime Gate 통과 뒤 3,800 `HARD_CANDIDATE`만 고치는 방식은 금지한다. 계산 근거가 포함된 tool payload 전체를 같은 runtime version으로 맞추기 위해 chart 4,350회와 period 900회를 모두 재생성한다.

```text
mix20k-v3.0.1-repaired/build-94eb7b543490 (불변)
  → Runtime Gate 통과
  → 해외 180행 한국 사례 교체 + 20행 UNSUPPORTED_REGION
  → chart 4,350 + period 900 전수 재실행
  → 저장된 tool result 2,200행과 후속 assistant 문장 재생성
  → call-only 3,050행도 실행·검증하되 결과를 임의 삽입하지 않음
  → mix20k-v3.1-runtime-grounded 새 build
  → leakage split·tokenizer·tool round-trip·Phase 4 preflight 재실행
```

생성기는 release 검증을 source dataset 읽기보다 먼저 수행한다. 기존 20K·source attribution·앞선 멀티턴은 보존하고, 각 행에 단일 `runtime_release_id`와 tool 사용 여부에 맞는 `runtime_fact_source`를 기록한다. 새 split manifest는 20K 학습 membership을 새 hash로 만들되 기존 비봉인 eval을 hash로 재사용하고 sealed blind는 hash-only로만 비교한다. preflight는 고정 tokenizer·768 token·assistant-only mask·EOS·직렬화·tool round-trip·비봉인 prompt overlap·sealed content hash overlap을 학습 없이 검사한다.

Runtime Gate만으로 학습을 허용하지 않는다. 생성 build와 preflight도 항상 `training_promotion_allowed=false`로 끝난다. 기존에 남은 대화 다양성, 4K/expert 1.5K 검수, state/grounding 품질 Gate와 별도의 학습 승격 판단이 필요하다.

## 13. 실행 명령

전용 CPU 환경은 학습용 PyTorch 환경과 분리한다.

```bash
uv venv .venv-runtime
uv pip install --python .venv-runtime/bin/python -r requirements-runtime-calculator-v1.2.txt

.venv-runtime/bin/python -m scripts.runtime.saju_runtime_v1_2 verify-contract
.venv-runtime/bin/python -m scripts.runtime.saju_runtime_v1_2 environment --include-validator
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector_v1_1 plan
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 plan
```

수집기 이름의 `v1_1`은 Git 제외 공식 snapshot의 불변 수집 형식을 뜻한다. v1.2 Gate는 해당 원문·manifest를 다시 검증해 읽으며 v1.1의 잘못된 날짜 판정 표기를 상속하지 않는다.

인증이 필요 없는 84개 표시 분 reference는 원문 7개와 함께 Git 제외 경로에 수집한다.

```bash
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 collect \
  --confirm-network COLLECT_KASI_MINUTE_REFERENCES_V1_1
```

인증 API key는 기본 경로 `/run/user/<UID>/saju-kasi-service-key`에 현재 사용자 소유 0600 일반 파일로 둔다. percent-encoding 전의 원문 key만 허용하며 key를 명령행·채팅·manifest·stdout·URL 로그에 기록하지 않는다. 두 수집은 같은 key로 순차 실행하고 resume manifest를 보존한다.

```bash
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector_v1_1 collect \
  --source lunar \
  --output data/raw/saju_runtime/kasi/v1.1.0/lunisolar \
  --confirm-network COLLECT_KASI_RUNTIME_V1_1

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 collect \
  --output data/raw/saju_runtime/kasi/v1.2.0/solar-terms-api \
  --confirm-network COLLECT_KASI_TERM_COVERAGE_V1_2

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_almanac_1964_collector collect \
  --output data/raw/saju_runtime/kasi/v1.2.0/almanac-1964 \
  --confirm-network COLLECT_KASI_ALMANAC_1964_V1
```

음양력 전수·24절기 실제 coverage·1964년 역서·표시 분을 함께 검증한다. 현재 v5 보고서는 차단 상태이며 이번 R4~R5 범위에서는 release `approve` 자체를 실행하지 않는다.

```bash
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.conformance_v5 run \
  --kasi-lunar-snapshot data/raw/saju_runtime/kasi/v1.1.0/lunisolar/kasi_lunisolar.jsonl \
  --kasi-solar-term-snapshot data/raw/saju_runtime/kasi/v1.2.0/solar-terms-api/kasi_solar_terms.jsonl \
  --kasi-minute-snapshot data/raw/saju_runtime/kasi/v1.1.0/minute-references/kasi_minute_references.jsonl \
  --kasi-almanac-1964-snapshot data/raw/saju_runtime/kasi/v1.2.0/almanac-1964/kasi_almanac_1964_baengno.json \
  --ephemeris /로컬/검증전용/de440s.bsp

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.intake_fsm_gate run
```

release가 생긴 뒤에만 v3.1 생성과 비학습 preflight를 순서대로 실행한다. 32바이트 production HMAC key는 저장소 밖의 현재 사용자 소유 0600 일반 파일이어야 하며, 값·hash를 로그나 manifest에 남기지 않는다. `--id-key-file` 대신 `SAJU_RUNTIME_ID_KEY_FILE` 하나만 사용할 수도 있다. 아래 명령도 학습·backward·optimizer step은 호출하지 않는다.

```bash
.venv-runtime/bin/python -m scripts.data.mix20k_v3_runtime_build build \
  --source-build data/derived/saju_1b_baseline/mix20k-v3.0.1-repaired/build-94eb7b543490 \
  --release-registry configs/runtime/calculation/releases/v1.2.0/release_registry.json \
  --id-key-file /run/user/<UID>/saju-runtime-id-hmac-key

.venv-data/bin/python -m scripts.training.phase5_v3_1_preflight run \
  --build-root data/derived/saju_1b_baseline/mix20k-v3.1-runtime-grounded/build-새해시 \
  --tokenizer-path models/saju_1b_baseline/kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720 \
  --release-registry configs/runtime/calculation/releases/v1.2.0/release_registry.json
```

기존 dashboard v1.8 canary는 v1.1 release 소비 코드이므로 v1.2 승인 근거로 재사용하지 않는다. 구조화 FSM과 암호화 persistence·보존 정책을 실제 앱/대시보드 adapter에 연결하고 별도 통합 Gate를 통과하기 전에는 `--enable-runtime-canary`로 재기동하지 않는다.

## 14. 완료 기준

- [x] 단일 Korea-only profile과 지원 범위를 고정했다.
- [x] 버전·URL·wheel SHA-256·MIT 고지를 고정했다.
- [x] 기본 off와 explicit candidate mode를 분리했다.
- [x] 음양력·절입·4주·오행·지장간·십신·기간 core를 구현했다.
- [x] unknown/range와 DST fold/gap을 추측 없이 처리한다.
- [x] LLM-visible allowlist에서 내부 trace·ID를 숨긴다.
- [x] KASI 인증 수집기와 표시 분 수집기, 계층형 conformance v3를 구현했다.
- [x] KASI 표시 분 84건과 독립 Skyfield/JPL 1,800건을 검증했다.
- [x] 엔진 간 날짜 차이를 공식 KASI 행 없이는 판정하지 않고 Gate에서 차단한다.
- [x] KASI 표시 분은 signed delta 진단과 분리해 KST 최근접 분 라벨 동등성으로 검증한다.
- [x] 1,800건 공개 JSONL·연도/연대 통계·SVG와 ΔT 원인 진단을 재현 가능하게 기록한다.
- [x] 5종 출생 파생 ID를 domain-separated HMAC-SHA256 v2로 교체했다.
- [x] 자유문 파서 없는 session v2.1/FSM v1.1과 앱 합성 Gate 100/100·구조/변조 check 18/18을 구현했다.
- [x] release registry와 승인 runtime의 hash chain·기본 off를 구현했다.
- [x] v3.0.1 20K를 읽기 전용 분석하고 v3.1 이관 수량을 고정했다.
- [x] v3.1 전수 재생성기와 새 split·비학습 preflight를 v1.2 release·HMAC 계약으로 고정했다.
- [x] 대시보드 v1.8에 구조화 chart·period·세션 snapshot canary를 기본 off로 구현했다.
- [x] KASI 54,787일 공식 snapshot을 확보하고 양음력·일진 mismatch 0을 확인한다.
- [x] KASI 24절기 API의 1900~2049 전 연도를 scan하고 실제 반환 범위 2000~2028년을 provider 보충 없이 고정한다.
- [x] KASI 1964년 역서 원문으로 백로 `9월 7일 24:00`을 확보하고 normalized civil date를 판정한다.
- [x] Astronomy Engine과 Skyfield/DE440s를 1,800건 비교하고 어느 provider도 전체 적격 Gate를 통과하지 못함을 v5에 기록한다.
- [ ] KASI 24절기 3,600건과 12절 날짜 1,800건 공식 snapshot을 확보한다. 현재 OpenAPI 반환은 696건·348건이다.
- [ ] Runtime Gate를 통과하고 profile ADR을 승인한다.
- [ ] production HMAC key 수명주기와 암호화 persistence·보존/삭제 정책을 운영 환경에서 승인한다.
- [ ] 앱 adapter에 구조화 event FSM을 연결하고 통합 Gate를 통과한다.
- [ ] v3.1을 새 fingerprint로 생성하고 split/preflight를 재실행한다.
- [ ] 승인 release와 feature flag 기본 off 상태로 앱·대시보드 canary를 검증한다.

## 진행 기록

- 2026-08-31
  - 작업 요약: 루트 조사 초안을 현재 KI20·MIX20K-v3.0.1 상태에 맞는 Korea-only 실행 정본으로 축소·재작성하고, versioned 계약과 Python candidate runtime을 구현했다.
  - 변경 범위: `configs/runtime/calculation`, `scripts/runtime/calculation`, KASI 수집기, conformance v2, v3.1 읽기 전용 이관 분석기, 전용 requirements와 제3자 고지를 추가했다. 기존 tool/session schema, 데이터 build, checkpoint, sealed blind는 변경하지 않았다.
  - 검증: `uvx ruff check scripts tests`, runtime·conformance·v3 이관 unit 26건, JSON/manifest/hash chain 검증을 통과했다. `master` 병합 뒤 기존 로컬 산출물이 있는 환경에서 전체 `unittest` 312건도 41.058초에 모두 통과했다. 공개 KASI 지원 범위 63건의 음양력·일진 mismatch 0, 단일 profile 16/16, unknown/range 500, hash 200, 해외 20, host TZ·locale·DST·heuristic leak 검사도 통과했다. 최종 구현 hash를 반영한 보고서 `build-8db2f43d91ca`는 공식 전수 수량 부족으로 `runtime_gate_passed=false`다.
  - 남은 이슈·후속 작업: KASI service key 또는 검증된 공식 전체 snapshot과 12절 경계 자료가 필요하다. 확보 전 v3.1 생성, 앱 기본 활성화, 실제 학습을 수행하지 않는다.

- 2026-08-31
  - 작업 요약: R4~R7의 실행 코드를 v1.1 계층형 Gate로 구현했다. KASI 인증 API 수집, KASI 달력자료 84건 원문 수집, Skyfield/JPL 독립 검증, 승인 release, MIX20K-v3.1 전수 재생성·split·비학습 preflight, dashboard v1.8 runtime canary를 연결했다.
  - 변경 범위: `requirements-runtime-calculator-v1.1.txt`, runtime v1.1 계약·engine·CLI, conformance v3·collector·release, v3.1 generator/loader/preflight, dashboard config·server·assets·테스트, 제3자 고지와 이 정본을 갱신했다. 기존 v3.0.1 build, KI20 model/checkpoint, 실행 중 dashboard process, sealed blind payload는 변경하지 않았다.
  - 검증: KASI 공식기관 표시 HTML 7개를 Git 제외 경로에 수집해 84/84행을 원문 SHA-256과 재파싱으로 확인했다. Astronomy Engine 최대 차이 59.457159초, Skyfield/JPL 최대 차이 29.281515초로 두 60초 Gate를 통과했다. 1900~2049 독립 절입 1,800건은 평균 절대 17.130844초, p99 57.591955초, 120초 초과·identity·순서 오류 0이고 profile 전/경계/후 5,400건 mismatch 0이다. `uvx ruff check scripts tests`, runtime·dashboard 표적 49건, `node --check`, `git diff --check`와 로컬 비추적 산출물이 있는 `master` 전체 `unittest` 327건(42.790초)을 통과했다. 최종 보고서 `build-2702394cde89`는 세 공식 인증 snapshot Gate만 false이며 release·v3.1·학습을 차단한다.
  - 남은 이슈·후속 작업: `/run/user/<UID>/saju-kasi-service-key`에 0600 KASI key가 필요하다. 54,787일과 3,600개 절기 날짜를 수집해 세 mismatch Gate가 0일 때만 release를 만들고 v3.1 생성→비학습 preflight→canary 순서로 진행한다. 실제 데이터 재생성·학습은 수행하지 않았다.

- 2026-08-31
  - 작업 요약: R4~R7 구현과 산출물의 경계 조건을 다시 감사해 release 보고서, v3.1 데이터 이관, dashboard runtime binding의 fail-closed 검증을 보강했다.
  - 변경 범위: conformance report의 canonical build ID·manifest governance·공식 snapshot·구현 파일 집합을 release 시 재검증한다. v3.0.1 원본 manifest SHA-256을 고정하고, v3.1 build preimage·artifact 집합·runtime release·5,250개 tool trajectory 수량을 preflight에서 다시 결합한다. dashboard는 runtime canary가 꺼진 상태에서 새 세션과 기존 결합 세션 모두 계산 사실을 모델 prompt에 넣지 못하게 했으며 내부 generation subprocess에도 명시 flag를 전달한다. 기존 데이터·모델·checkpoint·실행 중 dashboard process는 변경하지 않았다.
  - 검증: 변경 구현 hash로 새 conformance 보고서 `build-333036eb7024`를 생성했다. 이전 `build-2702394cde89`와 구현 hash를 제외한 집계 값이 byte-equivalent JSON 의미로 일치하며, KASI 표시 분 84건·독립 절입 1,800건·경계 5,400건은 계속 통과한다. 회귀 표적 57건, Ruff, 계약·환경 검증, 실제 v3.0.1 manifest·20,000행 identity 재검증과 Git 제외 모델·원천·파생물을 포함한 저장소 전체 unittest 335건(43.545초)을 통과했다.
  - 남은 이슈·후속 작업: KASI 인증 snapshot 세 Gate는 계속 미충족이므로 `runtime_gate_passed=false`, release·v3.1 생성·학습 차단 상태를 유지한다.

- 2026-08-31
  - 작업 요약: 날짜 판정·분 표기·독립 엔진 격차 보고 결함을 v1.2 Gate로 교정하고, 출생 파생 ID HMAC v2와 자유문 파서 없는 구조화 intake FSM을 추가했다.
  - 변경 범위: 기존 v1·v1.1 산출물을 보존한 채 conformance v4·release v1.2·공개 1,800행 진단/산점도, 5종 HMAC ID, session v2/FSM·100건 앱 Gate를 새 버전으로 추가했다. 미래 MIX20K-v3.1 생성·preflight도 v1.2 release와 production key만 받도록 바꿨다. 기존 20K·모델·checkpoint·실행 중 dashboard·sealed blind는 변경하거나 실행하지 않았다.
  - 검증: 최종 코드 hash로 conformance `build-08ea29de9e94`와 FSM `build-571d0e82ee0e`를 재현했다. 독립 절입 1,800행·날짜 차이 1건/미판정 1건·runtime 최근접 분 mismatch 16건·Skyfield mismatch 0건·FSM 100/100을 확인했다. 차단 보고서의 release 승인과 release 없는 v3.1 생성이 모두 exit 2로 fail-closed했다. `uvx ruff check scripts tests`, runtime 계약·환경 검증, 전체 `unittest` 353건(38.957초), `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 1964년 백로의 공식 KASI 행과 공식 전수 snapshot이 없어 Runtime Gate는 차단 상태다. production key·암호화 persistence·보존 정책·앱 FSM adapter도 미승인이라 release·v3.1 생성·학습·dashboard 재기동을 수행하지 않는다.

- 2026-08-31
  - 작업 요약: v1.2 구현과 보고서를 다시 변조 관점에서 감사해 과거 session v2의 중복 `period` key, 보고된 Gate boolean 신뢰, 약한 FSM state 의미 검증과 stale tool 결과 수용 가능성을 교정했다.
  - 변경 범위: 과거 v2·FSM/Gate v1.0과 기존 보고서는 수정하지 않고 session v2.1·FSM/Gate v1.1·독립 intake hash registry를 추가했다. 활성 v1.2 JSON은 중복 key를 거부하고 release Gate를 집계에서 재계산한다. FSM은 slot·provenance·authority·현재 입력 fingerprint와 `scr2_` call ID를 검증한다. v3.1 preflight는 legacy/비정상 runtime ID를 거부한다. 기존 20K·모델·checkpoint·실행 중 dashboard·sealed blind는 변경하거나 실행하지 않았다.
  - 검증: JSON Schema draft 2020-12 자체 검증과 정상 state 전이 8단계, malformed JSON 변조 fuzz 예외 누출 0건, 표적 48건, Ruff 전체와 전체 `unittest` 365건(43.067초)을 통과했다. 최종 코드 hash로 conformance `build-ec510bc6922d`와 FSM `build-3366376bb01b`을 각각 두 번 실행해 같은 ID를 재현했고 artifact hash chain도 일치했다. release 승인·v3.1 생성·preflight는 release 부재로 모두 exit 2로 차단됐다.
  - 남은 이슈·후속 작업: Runtime Gate false 8개는 그대로다. KASI 54,787일·24절기 3,600건/12절 1,800건과 1964년 백로 공식 행을 확보하고, Astronomy Engine의 표시 분 mismatch 16/84를 해결할 provider 결정을 새 버전으로 검증해야 한다. 이후에만 production key 수명주기·암호화 persistence·실제 app adapter 통합 Gate, v3.1 생성·비학습 preflight 순으로 진행한다.

- 2026-08-31
  - 작업 요약: R4~R5 범위에서 KASI 인증 snapshot을 실제 수집하고, 1964년 백로를 KASI 디지털 역서로 판정한 뒤 Astronomy Engine·Skyfield/DE440s를 conformance v5에서 동등한 provider 후보로 비교했다.
  - 변경 범위: Git 제외 raw에 음양력 54,787일, 24절기 150년 coverage scan, 1964년 역서 JSON·원문 이미지·정규화 snapshot을 보존했다. 추적 범위에는 v1.3 source/Gate, 두 fail-closed 수집기, 고정 달력 bracket 기반 provider 비교, conformance v5 보고서 `build-ef1b8ddb527e`, 7개 회귀 테스트와 정책 문서를 추가했다. release 승인·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: KASI 음양력 54,787/54,787일의 양음력·일진 mismatch 0, 24절기 API 1900~2049 scan 150/150년과 실제 반환 2000~2028년 696건, 1964년 역서 `9월 7일 24:00` 원문·이미지 hash chain을 확인했다. 두 provider 1,800건은 평균 절대 17.130844초, p99 57.566090초, 최대 80.666231초이며 120초 초과·identity·순서 오류 0이다. Astronomy는 1964 공식 civil date를 통과하나 분 표기 16/84가 다르고, Skyfield는 분 표기 84/84를 통과하나 1964 공식 civil date가 달라 선택하지 않았다. v5를 두 번 실행해 같은 `build-ef1b8ddb527e`를 재현했다. `uvx ruff check scripts tests`, 전체 `unittest` 372건(46.209초), v1.2 계약·validator 환경, `uv pip check`, Phase 1 source 계약·원본 verify, `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 공식 24절기 Gate는 696/3,600, 12절은 348/1,800으로 미달이며 선택 가능한 provider도 없다. `technical_gate_passed=false`, `runtime_approved=false`, `release_approval_performed=false`와 candidate ADR을 유지한다. 공식 coverage 확장 또는 새 provider·정당화된 경계 정책 없이는 release·앱·v3.1·학습으로 진행하지 않는다.
