<!-- saju_runtime_calculator_adoption.md - 한국 만세력 계산 runtime의 단일 정본·Gate·데이터 이관 순서를 기록한다. -->

# 한국식 만세력 Runtime 계산기 도입 정본

| 항목 | 값 |
|---|---|
| 문서 버전 | `runtime-calculator-adoption-v2.12.0` |
| 정본화 기준일 | 2026-09-02 |
| 현재 구현 기준 `master` | `a2239118581762d940bf13ba1e9dd32ed0d71d77` |
| 기준 모델 run | `KI20-MIX-v2/run-1f5d732cae67` |
| 모델 run 상태 | `trained_and_reloaded`, production 승격 금지 |
| runtime profile | `KR_CIVIL_MIDNIGHT_V1` |
| runtime 상태 | Skyfield/DE440s+builtin UT1 v1.3 후보와 원국 전용 v1.4 release를 보존한다. dashboard v1.9 production canary `build-ea53c272c1d6`가 합성 HTTP 100/100과 실제 K0·KI20 1쌍을 통과했다. 설정 기본값은 off로 유지하면서 검증된 운영 process만 명시 flag로 무인증 공개 chart-only 제한 활성화했다. strict/full runtime Gate와 기간 계산은 계속 차단 |
| 데이터 상태 | v3.1 생성·비학습 preflight 구현은 보존하되 chart-only release는 기간 tool을 승인하지 않으므로 v3.1 생성·preflight·학습은 미실행 |

이 문서는 앞서 제공된 `SAJU_RUNTIME_CALCULATOR_ADOPTION_PLAN.md` 조사 초안을 대체하는 저장소 실행 정본이다. 기존 데이터 보정 정본인 [`mix20k_v3_repair_plan.md`](mix20k_v3_repair_plan.md)와 역할을 나눈다.

- v3 repair 정본: LLM tool/state trajectory와 20K 데이터 계약
- 이 정본: tool이 반환할 원국·기간 계산 사실의 권위, 구현, 검증, 앱 연결

## 1. 결론

첫 runtime은 다음처럼 고정한다.

- production 언어는 Python이다.
- 지원 지역은 `country_code=KR`, timezone `Asia/Seoul`뿐이다.
- 후보 계산 범위는 1900~2049를 보존하되, 승인된 chart-only 범위는 정규화 양력 `1920-01-07~2026-08-31`이다.
- 양력·음력, 정확한 시각·범위·생시 미상을 지원한다.
- 오전/오후는 새 tool enum을 추가하지 않고 `range`로 정규화한다.
- 연 경계는 입춘 순간, 월 경계는 12절 순간, 일 경계는 민간시 00:00, 시주는 민간시를 쓴다.
- 진태양시·균시차는 적용하지 않는다.
- 지지 십신은 지장간 본기를 기준으로 한다.
- 외부 `manseryeok@2.0.0`은 개발 비교기로만 사용하며 Node runtime 의존성으로 넣지 않는다.
- 신강약·격국·용신·대운·공망·12운성·합충형파해·자동 해석은 v1 fact payload에서 제외한다.

기존 v1 candidate와 v1.1 산출물은 보존하고, v1.2 승인 wrapper·동적 release registry·HMAC ID·구조화 intake FSM을 새 버전으로 구현했다. R4~R5 실행으로 음양력 54,787일, OpenAPI 150년 전수 scan, KASI 공식 현재 계산 24기 다운로드 1920~2100년, 1964년 역서 근거를 확보했다. v1.4 Gate에서 “가용한 공식 데이터를 모두 수집했는가”와 “그 데이터에 맞는 provider가 있는가”를 분리했고, v1.5 Gate는 provider 후보 선정과 strict runtime 승인을 다시 분리한다. Skyfield/DE440s의 같은 TT root를 Skyfield 1.55 내장 UT1로 표시하면 공식 현재 계산 1,560행 중 날짜 mismatch 0, 원시 분 라벨 mismatch 22로 줄고 과거 1,280행의 14건은 KASI가 밝힌 1초 불확실성 범위 안이라 후보 Gate를 통과한다.

v1.3 candidate runtime은 이 후보를 실제 계산 경로에 결합했다. 절입 root 탐색과 경계 전·정확·후 판정은 TT로 수행하고, 공식 분 라벨은 `UT1_NOMINAL_PLUS_FIXED_KST`로만 분리해 표시한다. 1900~1919년은 `PROFILE_DETERMINISTIC`, snapshot 수집시점까지의 공식 과거는 `PAST_OFFICIAL_CORROBORATED`, 이후 미래는 `FORECAST_DIAGNOSTIC_NONAPPROVAL`로 결과에 구조화한다. conformance v8은 runtime과 별도 validator의 1,800개 TT root·UTC·표시 분이 모두 일치하고 5,400개 경계 배정 오류가 0임을 확인했다. 미래 280행은 지구 자전 예측 불확실성 때문에 물리 순간이 판정되지 않았고 원시 분 mismatch도 0이 아니므로 v1.3 자체와 strict/full runtime Gate는 계속 차단한다.

v1.4는 strict provider 승인을 우회하지 않고 과거 공식 근거가 완전한 원국 범위만 별도 권한으로 잘라 승인한다. 정규화 양력 날짜가 `1920-01-07~2026-08-31`이고 결과의 모든 절입 근거가 `PAST_OFFICIAL_CORROBORATED`·`SOURCE_HARD_FACT`일 때만 승격한다. exact 결과는 `HARD_GT`, range·unknown 결과는 ±1초 양끝에서 공통 사실이 같을 때만 `POLICY_BOUND_RULE`이다. 공식 과거 root가 표현 가능한 local minute와 ±1초 안에서 겹치는 50개 분은 exact와 불안정 range를 차단하고, 기간 tool은 모든 입력에서 `CHART_ONLY_PERIOD_OUT_OF_SCOPE`로 닫는다.

conformance v9는 부모 v8을 실제 원본으로 재계산한 뒤 scope matrix 328,722건, 허용 구간 태양력·음력 exact 77,908건, 과거 절입 1,279행·경계 probe 2,558건, range/unknown 2,660건을 자동 검증했다. 과거 원시 분 mismatch 14건은 숨기지 않고 보존했으며 격리 분 50건, 동일-분 range 차단 50건, unknown 안정 50건이 정확히 일치했다. 공개 보고서 `build-9f1784e74a4e`에 결합된 write-once release `saju-runtime-release-v1.4.0-63dc8d398e90`만 유효하다. feature 기본 off, production key 필수이며 앱 연결·v3.1 생성·학습은 승인하지 않는다.

운영 준비 v1.0은 runtime HMAC key와 session AEAD key를 32바이트 0600 단일-link 파일로 분리하고, AES-256-GCM·write별 12바이트 nonce·associated data·0700 root·0600 record·최대 100 session·1,800초 보존을 고정했다. 구조화 event-only adapter는 v1.4 release와 HMAC ID를 다시 검증하고 공개 응답에 allowlist 사실만 남긴다. 실제 DE440s 합성 local canary `build-ddde6dce3d3c`는 13개 층화 130/130을 통과했다.

dashboard binding v1.0은 기존 v1.8 파일을 보존한 새 v1.9 config·asset으로 이 adapter를 production process에 결합한다. public client authentication은 두지 않되 exact Host·Origin·CSRF, session/chart 30·event 300·model 10회/분, single-process lease, runtime 계산 직렬화와 `429`, stale revision `409`, legacy runtime route `410`을 고정했다. HMAC/AEAD key는 공개하지 않으며 출생정보·capability·request body도 로그에 남기지 않는다. K0와 KI20에는 같은 allowlist snapshot만 전달한다. production canary `build-ea53c272c1d6`는 합성 HTTP 100/100과 실제 GPU K0·KI20 1쌍을 통과했으며, 같은 구현을 적재한 loopback process만 명시 flag로 제한 활성화했다. 설정과 재기동의 기본값은 계속 off다.

후속 디버깅에서는 다른 provider 경계, 빈 role, 변조된 권한 요약과 JSON integer/boolean 혼동을 절입 증거로 받아들이던 경로를 차단했다. provider가 계산하지 않은 경계와 비정상 연도·index 타입도 거부하고, provider 종료 뒤 기간 계산은 예외를 누출하지 않고 `blocked`로 닫는다. 이 보강은 정상 계산값을 바꾸지 않았으며 최종 conformance 보고서는 최초 v8과 구현 hash를 제외한 집계가 같다.

과거 공식 근거 후보만 확인하는 별도 진단 경로는 session v2.2/FSM v1.2와 `serve-candidate`로 고정했다. 모든 가능한 생시 대안과 절입 경계가 `PAST_OFFICIAL_CORROBORATED`이고 출생 가능 시각 전체가 공식 snapshot cutoff 이전일 때만 `HARD_CANDIDATE`를 표시한다. 1900~1919 profile 구간, cutoff 이후, 기간 요청, stale call과 변조 HMAC은 차단한다. 이 화면은 `127.0.0.1` 전용·최대 100개·30분 유휴 만료 메모리 세션만 사용하며 기존 dashboard 자산, 모델 context와 disk persistence에는 연결하지 않는다.

## 2. 불변 보존 범위

다음 산출물은 수정하거나 재해석하지 않는다.

- 기존 `saju-tools-v1`, `saju-session-state-v1`(학습·과거 세션 계약).
- `session_state_schema_v2.json`·FSM/Gate v1.0은 과거 불변 산출물로 보존한다. session v2의 중복 `period` key 결함을 고친 일반 앱 후보는 `session_state_schema_v2.1.0.json`·FSM/Gate v1.1·`intake_registry-v1.1.0.json`이다. 새 session v2.2/FSM v1.2는 runtime v1.3 중 과거 공식 근거만 확인하는 별도 진단용이며 production 앱 후보를 대체하지 않는다.
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
| KASI 공식 현재 계산 24기 | [월력요항 페이지](https://astro.kasi.re.kr/kor/life/post/almanac), [24기 다운로드](https://astro.kasi.re.kr/kor/almanac/solarTerms/download) | 1920~2100년 현재 계산 분 라벨. 원문 4,343/4,344행이며 유일한 누락은 비절입인 2030년 우수, 절입은 2,172/2,172행 완전. KST=UTC+9와 최근접 분·날짜 변경 시 `24:00` 보존 규칙을 원문 그대로 적용 |
| KASI 디지털 역서 | [공식 archive](https://astro.kasi.re.kr/kor/almanac/pageView/26) | 1964년 역서 `KASI_A188_Z_001` 20쪽의 백로 `9월 7일 24시 00분` 문서 사실은 `SOURCE_HARD_FACT`. KASI 현재 계산과 과거 역서가 다를 수 있다는 공식 고지에 따라 현재 provider 물리 판정 권한은 `INSTITUTIONAL_ADVISORY` |
| KASI 달력자료 | [공식기관 표시 페이지](https://astro.kasi.re.kr/kor/life/post/calendarData) | 2021~2027년 12절 84건의 표시 분 reference. 페이지 고지대로 `INSTITUTIONAL_ADVISORY`이며 provider hard block을 걸지 않음 |
| IANA tzdb | `2026c` | 역사 civil time, DST fold/gap |
| Python `tzdata` | `2026.3`, wheel SHA-256 `dc096730…e54931` | 재현 가능한 timezone 배포본 |
| `korean-lunar-calendar` | `0.4.0`, wheel SHA-256 `c042e20d…fe4e7` | KASI 전수 대조 전 음양력 후보 provider |
| Astronomy Engine | `2.1.19@61dc07020aaa6885d2c7f688a4d82beaf6edb9ef`, wheel SHA-256 `232ba7dd…6f67f` | KASI 경계 전수 대조 전 절입 후보 provider |
| Skyfield / JPL DE440s | `1.55`, DE440s SHA-256 `c1c7feea…0a49f2` | 1900~2049년 12절 1,800건의 provider 교차 비교와 v1.3 후보 계산, v1.4 chart-only 원국 계산. 고정 달력 bracket의 TT root·고정 hash 내장 UT1을 사용하며 자동 다운로드·Astronomy fallback 없음. production 앱에는 연결하지 않음 |
| IERS `finals2000A.all` | 2026-09-01 snapshot, SHA-256 `e3905ff7…fe058` | 현재 IERS UT1 민감도 진단 전용. 원문·manifest는 0600 Git 제외 불변 산출물이며 미래 예측 oracle이나 자동 fallback으로 사용하지 않음 |
| jplephem / NumPy / sgp4 / certifi | `2.24` / `2.2.6` / `2.27` / `2026.7.22` | Skyfield validator의 고정 전이 의존성 |
| `manseryeok` | `2.0.0@fba3253d7305b8b61189bd78318a7a27ed8c9b09` | 개발·비교 전용, production dependency 아님 |

v1.1 패키지와 원천은 그대로 보존한다. v1.2는 [`requirements-runtime-calculator-v1.2.txt`](../../requirements-runtime-calculator-v1.2.txt)와 [`source_registry-v1.2.0.json`](../../configs/runtime/calculation/source_registry-v1.2.0.json)에 같은 wheel·DE440s identity, 새 교차검증 구현 hash, 판정 범위와 근거 문서를 고정했다. v1.3.1 source registry와 Gate는 실제 API coverage scan·1964년 역서·동등한 provider 후보 비교에 중복 JSON key·중복 역서 page·canonical byte·원 수집기 hash 검증을 추가했다. v1.4.0은 공식 현재 계산 24기 원문과 반올림 규약을 새 불변 snapshot으로 추가하고 데이터 가용성 Gate를 provider 적격성 Gate에서 분리한다. v1.5.0은 Skyfield 내장 UT1, 현재 IERS UT1, Astronomy/Espenak-Meeus UT, proleptic UTC 표시를 같은 TT root에서 비교하고 provider 후보 선정과 strict runtime 승인을 분리한다. v1.6.0은 [`requirements-runtime-calculator-v1.3.txt`](../../requirements-runtime-calculator-v1.3.txt), runtime contract/schema/profile v1.3과 source/Gate v1.6을 새 hash chain으로 고정하고 Skyfield를 candidate runtime에 결합한다. v1.7.0은 [`requirements-runtime-calculator-v1.4.txt`](../../requirements-runtime-calculator-v1.4.txt), runtime contract/schema/profile v1.4와 source/Gate v1.7, conformance v9, chart-only release schema를 고정한다. 기존 v1/v1.2/v1.3 구현과 보고서는 불변 이력으로 유지한다. 1900~1919년 240개 절입은 공식 snapshot에 생성값을 써넣지 않고 `PROFILE_DETERMINISTIC`으로 명시한다. Astronomy Engine은 공식 설명대로 compact·truncated VSOP87/NOVAS 계열이고 약 ±1 arcminute 설계 목표를 가지며, Skyfield는 여러 time scale과 ΔT를 별도로 관리한다. 동일 TT root로 재투영해도 평균 절대 차이의 67.209818%와 분 라벨 차이 330건이 남으므로 원 UTC 차이를 ΔT 하나로 설명하지 않는다. Skyfield 48회 이분법 root의 최대 황경 잔차는 `4.2564e-8` 각초이고 32회와의 최대 차이는 `101.09µs`라서 약 11초의 동일 TT 차이를 근찾기 허용오차로 설명하지 않는다. 제3자 라이선스와 JPL 비추적 조건은 [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md)에 보존한다. DE440s와 KASI·IERS 원문 snapshot은 Git에 넣지 않는다.

외부 패키지가 결과를 냈다는 사실은 승인 근거가 아니다. 공식·별도 fixture와 프로젝트 profile을 통과한 필드만 승인할 수 있다.

## 5. 구조

```text
saju-tools-v1
  → 입력 검증·한국 범위 정규화
  → IANA Asia/Seoul fold/gap 판정
  → 한국 양음력 후보 provider
  ├─ v1/v1.2 이력: Astronomy Engine 절입 순간·승인 wrapper
  └─ v1.3 활성 후보: Skyfield/DE440s TT 절입 경계·내장 UT1 표시
  → KR_CIVIL_MIDNIGHT_V1 versioned 4주 core
  → 오행·지장간·십신 파생
  → exact chart 또는 range/unknown chart set
  → conformance v4: 공식 날짜 판정 + 최근접 분 표기 + 비권위 120초 회귀 가드
  → conformance v5: 실제 API coverage + 1964 공식 역서 + Astronomy/Skyfield provider 적격성
  → conformance v6: 공식 현재 계산 24기 + 데이터 가용성/provider 적격성 분리 + 동일 TT 축 진단
  → conformance v7: UT1 표시 후보 비교 + 과거 불확실성/미래 예측 분리 + 후보/strict runtime Gate 분리
  → v1.3 candidate runtime: Skyfield/DE440s TT 경계 + 구조화 과거/미래 권한
  → conformance v8: runtime↔별도 validator 1,800 TT root + 전/정확/후 5,400건 + 권한 분할 검증
  → v1.4 chart-only runtime: 과거 공식 완전 일자 원국만 승인 + ±1초 경계 격리 + 기간 차단
  → conformance v9: scope 328,722 + exact 77,908 + 경계 2,558 + range/unknown 2,660
  → HMAC-SHA256 v2 출생 파생 ID
  → 구현·계약·공식 snapshot hash 결합 release registry
  → ApprovedSajuRuntimeEngineV14(feature flag 기본 off, production key 필수)
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

기존 candidate 결과는 항상 `status=partial`, `fact_authority=HARD_CANDIDATE`다. v1.3은 `hard_facts.solar_term_evidence`에 결정 경계의 TT·UTC·공식 표시 분·권한을 기록하고, 여러 생시 후보나 기간 경계는 가장 보수적인 권한 순서 `FORECAST_DIAGNOSTIC_NONAPPROVAL` → `PROFILE_DETERMINISTIC` → `PAST_OFFICIAL_CORROBORATED`로 병합한다. provider가 생성한 값 자체는 어느 구간에서도 공식 원문으로 표시하지 않는다. v1.3 자체는 exact도 `HARD_GT`로 승격하지 않는다.

v1.4 release wrapper는 모든 후보 경계가 과거 공식 hard evidence이고 승인 날짜 안일 때만 새 release identity로 HMAC ID를 다시 발급한다. exact 단일 원국은 `status=ok`, `fact_authority=HARD_GT`; range·unknown은 대안 전체의 공통 사실만 `status=partial`, `fact_authority=POLICY_BOUND_RULE`로 공개한다. ±1초 격리 분과 겹치는 exact 또는 양끝에서 공통 사실이 달라지는 불확실 입력은 사실 등급 없이 차단한다. release가 없거나 feature가 꺼져 있으면 각각 `RUNTIME_RELEASE_REQUIRED`, `RUNTIME_FEATURE_DISABLED`이고, 기간 계산은 release·flag와 무관하게 항상 차단한다.

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

source/Gate v1.4부터 데이터 가용성과 provider 판단을 분리하고, v1.5부터 provider 판단도 후보 선정과 strict runtime 승인으로 나눈다. v1.7은 strict/full Gate를 바꾸지 않고 과거 공식 완전 일자의 chart-only 승인 축을 추가한다. 데이터 가용성이나 후보 Gate 통과는 production provider 변경을 뜻하지 않는다.

| 축 | 기준 | 현재 상태 |
|---|---|---:|
| 데이터 가용성 | KASI 음양력 54,787일, 24절기 OpenAPI 1900~2049 전수 scan, 공식 현재 계산 다운로드 1920~2100 원문과 알려진 누락을 모두 보존 | 통과 |
| 공식 절입 coverage | 다운로드 원문 4,343/4,344행, 유일한 누락 `2030년 우수`가 비절입이고 절입 2,172/2,172행 완전 | 통과 |
| 미coverage 등급 | 1900~1919 절입 240행은 `PROFILE_DETERMINISTIC`; 생성값을 공식 snapshot에 기록하지 않음 | 통과 |
| provider 후보 | 1920~snapshot 수집 시점의 과거 공식 행은 날짜 mismatch 0이고 KASI의 과거 불확실성 1초를 넘는 반올림 구간 이탈 0, 미래 행은 승인 근거가 아닌 예측 진단으로 분리, 1,800행 identity·순서 오류 0 | 통과: `skyfield_de440s_builtin_ut1` |
| strict runtime provider | 공식 1,560행의 원시 최근접 분 라벨 mismatch 0, 미래 물리 순간 판정 완료, strict 적격 provider 선택 | 실패: 22건·미래 미판정 |
| 자문 원천 권한 | 비공식 달력자료 84행과 과거 역서는 결과를 기록하되 provider hard block에 사용하지 않음 | 통과 |
| candidate runtime 결합 | 선택된 Skyfield provider의 1,800개 TT root·UTC·표시 분이 별도 validator와 일치하고 전/정확/후 5,400건 배정 mismatch 0. production binding은 없음 | 통과 |
| chart-only runtime | 정규화 양력 `1920-01-07~2026-08-31`, 과거 공식 근거 원국만 승인. exact 77,908건 실패 0, ±1초 격리 분 50건, period 항상 차단 | 통과: v1.4 release, feature 기본 off |
| full runtime·production binding | 미래 절입·기간 계산·앱 adapter까지 포함한 전체 승인 | 실패/미실행 |

provider 선택 뒤에도 아래 공통 conformance가 모두 참이어야 기술 Gate를 통과한다.

| Gate | 기준 |
|---|---:|
| KASI 음양력·일진 | 1900-01-01~2049-12-31, 54,787일, mismatch 0 |
| Skyfield/JPL 교차 절입 | 12절 × 150년 = 1,800, 고정 120초 회귀 가드 이내, 절기 identity·시간 순서 오류 0. 120초는 정확도 oracle 아님 |
| baseline 내부 profile 전/경계/후 | 12절 × 150년 × 3 = 5,400, 배정 mismatch 0. 경계 순간 정확도 검증으로 표현 금지 |
| unknown/range | 500 이상 |
| HMAC v2 ID | 200 이상, 재현·prefix·domain·key 분리 오류 0 |
| 해외 unsupported | 20 이상 |
| profile boundary mismatch | 0 |
| unknown 시주 추측 | 0 |
| DST gap 자동 이동 | 0 |
| DST fold 자동 선택 | 0 |
| host TZ·locale byte drift | 0 |
| heuristic fact leak | 0 |
| 미분류 mismatch | 0 |
| silent fallback | 0 |
| source/profile version ID 불변 오류 | 0 |

KASI service key가 없어도 “데이터 없음”을 provider 값으로 덮지 않는다. 인증 API 수집기는 월/연 단위 resume·최대 10,000요청/run·명시 확인·0600 key 파일 우선 방식으로 구현했다. 2026-08-31 실제 수집에서 음양력은 전 범위를 반환했고 24절기 OpenAPI는 1900~2049년 150개 요청 중 2000~2028년만 24건씩 반환했다. coverage 수집기는 0건 연도도 response hash와 함께 기록한다. 별도 KASI 공식 다운로드는 인증 없이 1920~2100 현재 계산값을 제공하며, 원문·정규화 JSONL·manifest를 0600 불변 산출물로 보존한다.

공식 다운로드의 최근접 분과 날짜 변경 시 `24:00` 보존 규약은 원문에 명시돼 있다. `24:00`은 인쇄된 날짜의 끝, 즉 다음 civil day `00:00`으로 정규화하며 이 변환은 1964년 현재 계산과의 불일치를 만든 원인이 아니다. OpenAPI 696행과 교차검증하면 2011년 대한 1건에서 OpenAPI `1월 21일 19:18`과 현재 계산 다운로드 `1월 20일 19:19`가 충돌한다. 라이브 OpenAPI 원 응답도 같은 값을 반환함을 재확인했다. 두 원천을 모두 수집했으므로 이 충돌은 데이터 가용성 실패로 세지 않고, 더 최신이며 분 라벨·계산 고지를 함께 제공하는 공식 다운로드를 provider hard evidence로 우선한다. 인증이 필요 없는 달력자료 84행은 `INSTITUTIONAL_ADVISORY`다. 1964년 과거 역서의 인쇄 사실은 hard fact로 보존하지만 KASI 현재 계산과 과거 역서가 다를 수 있다는 고지 때문에 현재 물리 provider 판정에는 advisory로만 사용한다.

KASI 원문은 과거 계산의 시간 오차가 일반적으로 1초 이내이며 미래 계산은 지구 자전의 불규칙성 때문에 수초~수분 달라질 수 있다고 밝힌다. 따라서 v1.5 후보 Gate는 snapshot 수집 시점 기준 과거 1,280행과 미래 280행을 분리한다. 과거에는 공개 분 라벨이 뜻하는 ±30초 반올림 구간에 공식 불확실성 1초를 더한 범위를 적용하고, 미래 280행은 후보 탈락 근거도 승인 근거도 아닌 예측 진단으로만 기록한다. strict runtime Gate는 이 완화 규칙을 쓰지 않고 원시 분 라벨 mismatch 0과 미래 물리 순간 판정을 계속 요구한다.

## 10. 현재 검증 결과

공개 보고서:

```text
data/reports/saju_runtime_conformance/v1.2.0/build-08ea29de9e94/
data/reports/saju_runtime_conformance/v1.2.0/build-ec510bc6922d/
data/reports/saju_runtime_conformance/v1.3.0/build-ef1b8ddb527e/
data/reports/saju_runtime_conformance/v1.3.1/build-1e754de17c82/
data/reports/saju_runtime_conformance/v1.4.0/build-3366c5069a26/
data/reports/saju_runtime_conformance/v1.5.0/build-01111af7e09c/
data/reports/saju_runtime_conformance/v1.6.0/build-a49aed186743/
data/reports/saju_runtime_conformance/v1.6.0/build-8bd88d6db03a/
data/reports/saju_runtime_conformance/v1.7.0/build-9f1784e74a4e/
data/reports/saju_runtime_intake_fsm/v1.1.0/build-3366376bb01b/
data/reports/saju_runtime_migration/v1.0.0/build-94eb7b543490/analysis.json
data/reports/saju_runtime_app_canary/v1.0.0/build-ddde6dce3d3c/
```

`build-08ea29de9e94`, FSM v1.0 `build-571d0e82ee0e`, conformance v5.0.0 `build-ef1b8ddb527e`, v5.0.1 `build-1e754de17c82`, v6.0.0 `build-3366c5069a26`, v7.0.0 `build-01111af7e09c`과 최초 v8 `build-a49aed186743`은 당시 코드의 이력 산출물로 보존한다. 현재 후보 provider 판단은 v8.0.0 `build-8bd88d6db03a`, 현재 chart-only release 판단은 이를 실제 원본으로 재계산한 v9.0.0 `build-9f1784e74a4e`와 `configs/runtime/calculation/releases/v1.4.0/release_registry.json`을 따른다.

| 검사 | 결과 |
|---|---:|
| KASI 음양력 공식 snapshot | 54,787/54,787일, 양음력·일진 mismatch 0/0 |
| KASI 24절기 API 전 연도 scan | 150/150년 요청, 실제 지원 2000~2028년 |
| KASI 공식 현재 계산 24기 원문 | 4,343/4,344행, 유일한 누락은 2030년 우수(비절입) |
| KASI 공식 현재 계산 절입 | 1920~2100년 2,172/2,172행, runtime 범위 1920~2049년 1,560행 |
| KASI OpenAPI ↔ 현재 계산 교차검증 | 696/696행 매핑, 2011년 대한 날짜·분 충돌 1건. 수집 가용성은 통과, 현재 계산 다운로드 우선 |
| 미coverage 절입 | 1900~1919년 240행, `PROFILE_DETERMINISTIC`, 공식 snapshot provider 보충 0 |
| KASI 비공식 표시 분 reference | 84/84, `INSTITUTIONAL_ADVISORY`, hard block 아님 |
| Astronomy Engine ↔ 공식 현재 계산 분 label | 1,257/1,560, mismatch 303, 날짜 mismatch 1 |
| Skyfield/JPL proleptic UTC ↔ 공식 현재 계산 분 label | 1,403/1,560, mismatch 157, 날짜 mismatch 0 |
| Skyfield/JPL + Astronomy/Espenak-Meeus UT | mismatch 97/1,560, 날짜 mismatch 0 |
| Skyfield/JPL + 현재 IERS 기반 UT1 | mismatch 29/1,560, 날짜 mismatch 0. snapshot 범위 1973-01-02~2027-09-04 밖은 Skyfield 외삽이므로 진단 전용 |
| Skyfield/JPL + Skyfield 1.55 내장 UT1 | mismatch 22/1,560, 날짜 mismatch 0. 과거 1,280행 중 14건은 모두 공식 1초 불확실성 안, 미래 280행 중 8건은 비승인 예측 진단 |
| v1.3 runtime ↔ 별도 validator | TT root 최대·평균 차이 `0.0µs`, UTC mismatch 0/1,800, UT1 표시 분 mismatch 0/1,800 |
| v1.3 runtime 권한 분할 | `PROFILE_DETERMINISTIC` 240, `PAST_OFFICIAL_CORROBORATED` 1,280, `FORECAST_DIAGNOSTIC_NONAPPROVAL` 280 |
| v1.4 scope matrix | 328,722건, 허용 233,724·차단 94,998·실패 0 |
| v1.4 허용 구간 exact | 태양력·음력 77,908건, 실패 0 |
| v1.4 과거 절입 경계 | 1,279행, floor/ceil probe 2,558건, 실패 0 |
| v1.4 ±1초 정책 | 원시 분 mismatch 14 보존, 격리 분 50, 동일-분 range 차단 50, unknown 안정 50 |
| v1.4 release | `saju-runtime-release-v1.4.0-63dc8d398e90`, chart만 승인·period 차단·feature 기본 off·production key 필수 |
| Astronomy Engine ↔ Skyfield/JPL | 1,800/1,800, 120초 초과 0 |
| provider 교차 비교 평균 절대 차이 / p99 / 최대 | 17.130844초 / 57.566090초 / 80.666231초 |
| 동일 TT root 평균 절대 차이 / p99 / 최대 | 11.513609초 / 37.763867초 / 52.145523초, 원 평균의 67.209818% 잔존 |
| profile 분 라벨 차이 | 원 UTC 494건 → Astronomy TT를 Skyfield UTC mapping으로 재투영해도 330건 잔존, `not_delta_t_only` |
| root 수렴 진단 | Skyfield 황경 잔차 최대 `4.2564e-8` 각초, 32회↔48회 root 차이 최대 `101.09µs`; 약 11초 동일 TT 차이의 원인이 아님 |
| 1964년 백로 | 현재 계산 `9월 7일 23:59`는 Skyfield 내장 UT1 표시와 일치. 과거 역서 `9월 7일 24:00`은 다음 날 `00:00`으로 올바르게 정규화하며 Astronomy exact KST `9월 8일 00:00:00.017704`; 서로 다른 vintage로 보존 |
| provider 판단 | Skyfield/DE440s+내장 UT1을 v1.3 candidate runtime에 결합, strict 적격 provider는 없음, production runtime provider 변경 없음 |
| 분리 Gate | 데이터 가용성·baseline·provider 후보·candidate runtime conformance·과거/미래 권한 Gate 통과, strict runtime provider·release Gate 실패 |
| provider 교차 비교 절기 identity·순서 오류 | 0 / 0 |
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

1964년 백로는 원천의 계산 vintage를 분리한다. KASI 현재 계산 다운로드는 `1964-09-07T23:59+09:00`이고 Skyfield 내장 UT1 표시가 그 분 라벨과 일치한다. 디지털 과거 역서 원문은 `9월 7일 24시 00분`이며 인쇄일 끝을 뜻하므로 `1964-09-08T00:00+09:00`으로 정규화하는 것이 맞다. Astronomy Engine exact KST는 `1964-09-08T00:00:00.017704+09:00`이므로 현재 계산 날짜 mismatch는 정규화가 만든 오류가 아니다. 현재 계산 원문 자체가 과거 역서 기록과 최신 계산이 다를 수 있다고 설명하므로 과거 인쇄 내용은 문서 사실로 보존하되 현재 물리 provider의 hard adjudicator로 사용하지 않는다. 두 분 단위 원천 모두 sub-minute 물리 정확도를 판정하지 않는다.

strict/full runtime의 남은 실패 원인은 데이터 수량이나 근찾기 수렴 오차가 아니다. 가용한 공식 원문은 모두 수집했고 공식 다운로드가 공개한 절입 범위도 완전하다. Skyfield/DE440s+내장 UT1은 과거 공식 행의 선언된 불확실성 범위와 candidate runtime의 5,400개 TT 경계 배정을 통과했다. 하지만 전체 1,560행의 최근접 분 라벨 mismatch 0은 달성하지 못했고, 특히 미래 280행의 물리 순간을 이미 확정된 공식 oracle처럼 취급할 수 없다. v1.4 chart-only release는 이 실패를 완화하지 않고 미래·profile·기간을 범위 밖으로 차단한 별도 승인이다. production provider 변경이나 전체 runtime 승인이 아니다.

- KASI 음양력 전수: `54,787 / 54,787`, mismatch 0
- KASI 24절기 OpenAPI scan: `150 / 150년`, 반환 `696행`
- KASI 공식 현재 계산: 원문 `4,343 / 4,344`, 절입 `2,172 / 2,172`
- runtime 공식 hard 절입: `1,560`, Astronomy 분 mismatch 303, Skyfield proleptic UTC 157, Skyfield 내장 UT1 22
- 공식 미coverage: 1900~1919 절입 240행, `PROFILE_DETERMINISTIC`
- 비공식 표시 분 자료: `84 / 84`, advisory 완료
- Skyfield/JPL 절입 수량·비권위 회귀 가드: `1,800 / 1,800`, 120초 초과 0
- 동일 TT 진단: 평균 절대 11.513609초·분 라벨 차이 330건 잔존, ΔT 단일 원인도 근찾기 수렴 오차도 아님
- provider 우선 후보: `skyfield_de440s_builtin_ut1`, 과거 불확실성 초과 0, strict 적격 provider 없음
- v1.3 candidate runtime↔별도 validator: TT·UTC·표시 분 mismatch 0/1,800
- v1.3 candidate runtime 배정 전/경계/후: `5,400 / 5,400`, 배정 mismatch 0
- 절입 권한: profile 240, 과거 공식 corroborated 1,280, 미래 forecast nonapproval 280
- v1.4 chart-only scope: `1920-01-07~2026-08-31`, 38,954일
- v1.4 exact 태양력·음력: `77,908 / 77,908`, 실패 0
- v1.4 경계: 과거 절입 1,279행, probe 2,558건, 격리 분 50건
- v1.4 불확실 입력: 2,660건, 실패 0, 동일-분 range 차단 50·unknown 안정 50
- v1.4 release: `saju-runtime-release-v1.4.0-63dc8d398e90`, feature 기본 off
- `data_availability_gate_passed=true`
- `baseline_conformance_gate_passed=true`
- `provider_candidate_gate_passed=true`
- `candidate_runtime_provider_bound=true`
- `candidate_runtime_conformance_passed=true`
- `past_authority_gate_passed=true`
- `future_authority_separation_gate_passed=true`
- `provider_eligibility_gate_passed=false`
- `strict_runtime_provider_gate_passed=false`
- `full_runtime_gate_passed=false`
- `chart_only_gate_passed=true`
- `chart_release_registry_creation_allowed=true`
- `chart_only_release_approval_performed=true`
- `runtime_feature_flag_default=false`
- `production_application_binding=true` (설정 기본 off, 검증된 live process만 명시 활성)
- `production_canary_gate_passed=true` (`build-ea53c272c1d6`, HTTP 100/100 + GPU 1쌍)
- `limited_chart_only_live_enabled=true` (검증된 process의 명시 flag에만 적용)
- `mix20k_v3_1_regeneration_allowed=false`
- `training_promotion_allowed=false`

## 11. 실행 순서와 상태

| 순서 | 작업 | 상태 |
|---:|---|---|
| R0 | KI20·v3.0.1·sealed blind 상태 동결 | 완료 |
| R1 | input/output/profile/source/ID/Gate 계약 고정 | v1.4 runtime·output·profile·release schema와 v1.7 source/Gate hash chain까지 완료 |
| R2 | Python 음양력·절입·4주·불확실성·기간 core 구현 | v1.3 후보를 보존하고 v1.4 chart-only wrapper에 과거 공식 날짜·±1초·권한 Gate 결합. period는 항상 차단 |
| R3 | 기존 tool allowlist in-process bridge | 완료(기본 off) |
| R4 | KASI 전수·계층형 절입 snapshot 수집 | 완료(음양력 54,787일, OpenAPI 150년 scan, 공식 현재 계산 1920~2100 절입 2,172행, 표시 분 84건, 1964 역서). 1900~1919 공식 절입 미coverage는 별도 등급으로 명시 |
| R5 | full conformance와 profile ADR 승인 | v8 후보 conformance와 v9 chart-only 자동 Gate 완료. chart-only release 생성, strict/full provider Gate는 계속 실패 |
| R6 | v3.1 5,250 tool call 전수 재생성·새 split/preflight | 생성기·preflight 구현만 보존. v1.4가 period를 승인하지 않고 현재 작업 범위에서도 제외했으므로 생성·preflight 미실행 |
| R7 | 대시보드 `KI20 + Runtime` local lane·앱 canary | 완료. v1.4 분리 키·AES-GCM adapter local 130/130과 v1.9 production `build-ea53c272c1d6` HTTP 100/100·실제 GPU 1쌍을 통과했다. 같은 구현의 loopback process를 명시 flag로 제한 활성화했고 공개 HTTPS smoke·로그 비노출·rollback 경계를 확인했다. 설정 기본값은 off |
| R8 | 새 모델 학습 handoff | 이 계획 범위 밖 |

chart-only release는 원국 계산 엔진의 제한된 기술 승인일 뿐 학습 Gold나 full runtime 승인이 아니다. v1.9 binding은 공개 UI와 모델 context 연결 코드를 제공하지만 명시 flag 전에는 runtime resource를 열지 않는다. 현재 제한 활성화는 `build-ea53c272c1d6`와 같은 구현·release·운영 경계에만 적용하며, 이후 canary나 live smoke가 실패하면 v1.8을 runtime flag 없이 복구한다.

## 12. MIX20K-v3.1 계약

향후 full Runtime Gate가 별도 버전에서 통과하더라도 3,800 `HARD_CANDIDATE`만 고치는 방식은 금지한다. 계산 근거가 포함된 tool payload 전체를 같은 runtime version으로 맞추려면 chart 4,350회와 period 900회를 모두 재생성해야 한다. 현재 v1.4 release는 chart-only이고 period 900회를 의도적으로 차단하므로 이 계약의 입력 release가 아니며 v3.1 생성 권한을 열지 않는다.

```text
mix20k-v3.0.1-repaired/build-94eb7b543490 (불변)
  → chart·period를 모두 승인한 미래 full Runtime Gate 통과
  → 해외 180행 한국 사례 교체 + 20행 UNSUPPORTED_REGION
  → chart 4,350 + period 900 전수 재실행
  → 저장된 tool result 2,200행과 후속 assistant 문장 재생성
  → call-only 3,050행도 실행·검증하되 결과를 임의 삽입하지 않음
  → mix20k-v3.1-runtime-grounded 새 build
  → leakage split·tokenizer·tool round-trip·Phase 4 preflight 재실행
```

생성기는 release 검증을 source dataset 읽기보다 먼저 수행한다. 기존 20K·source attribution·앞선 멀티턴은 보존하고, 각 행에 단일 `runtime_release_id`와 tool 사용 여부에 맞는 `runtime_fact_source`를 기록한다. 새 split manifest는 20K 학습 membership을 새 hash로 만들되 기존 비봉인 eval을 hash로 재사용하고 sealed blind는 hash-only로만 비교한다. preflight는 고정 tokenizer·768 token·assistant-only mask·EOS·직렬화·tool round-trip·비봉인 prompt overlap·sealed content hash overlap을 학습 없이 검사한다.

미래 full Runtime Gate만으로도 학습을 허용하지 않는다. 생성 build와 preflight도 항상 `training_promotion_allowed=false`로 끝난다. 기존에 남은 대화 다양성, 전체 state/grounding·언어·정책 자동 Gate와 별도의 학습 승격 판단이 필요하다. 자동 계약이 없는 의미 품질은 `not_measured`로 기록한다.

## 13. 실행 명령

전용 CPU 환경은 학습용 PyTorch 환경과 분리한다.

```bash
uv venv --python 3.10 .venv-runtime
uv pip install --python .venv-runtime/bin/python -r requirements-runtime-calculator-v1.4.txt

.venv-runtime/bin/python -m scripts.runtime.saju_runtime_v1_4 verify-contract
.venv-runtime/bin/python -m scripts.runtime.saju_runtime_v1_4 environment
.venv-runtime/bin/python -m unittest tests.test_saju_runtime_v1_4 -v
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector_v1_1 plan
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 plan
```

chart-only adapter 운영 준비 환경은 암호화 의존성을 포함한 별도 lock 파일로 재현한다. 다음 명령은 계약과 기본 off 계획만 확인하며 운영 key·session store·production 앱을 열지 않는다.

```bash
uv venv --python 3.10 .venv-runtime-adapter
uv pip install --python .venv-runtime-adapter/bin/python \
  -r requirements-runtime-adapter-v1.0.txt

.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations validate-contract
.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations environment
.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations plan

.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary validate-contract
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary plan
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary verify \
  --report-root data/reports/saju_runtime_app_canary/v1.0.0/build-ddde6dce3d3c
```

합성 canary를 새 build로 실행할 때만 Git 제외 DE440s의 절대 경로를 `run --ephemeris`에 전달한다. 운영 key 생성·검증과 보존·rotation 절차는 `docs/runtime/chart_only_operations.md`를 따르며 실제 secret 경로는 이 문서나 명령 기록에 남기지 않는다.

수집기 이름의 `v1_1`은 Git 제외 공식 snapshot의 불변 수집 형식을 뜻한다. v1.2 Gate는 해당 원문·manifest를 다시 검증해 읽으며 v1.1의 잘못된 날짜 판정 표기를 상속하지 않는다.

인증이 필요 없는 84개 표시 분 reference는 원문 7개와 함께 Git 제외 경로에 수집한다.

```bash
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_minute_collector_v1_1 collect \
  --confirm-network COLLECT_KASI_MINUTE_REFERENCES_V1_1
```

인증 API key는 기본 경로 `/run/user/<UID>/saju-kasi-service-key`에 현재 사용자 소유 0600 일반 파일로 둔다. percent-encoding 전의 원문 key만 허용하며 key를 명령행·채팅·manifest·stdout·URL 로그에 기록하지 않는다. 두 수집은 같은 key로 순차 실행하고 resume manifest를 보존한다. 기존 `v1.2.0` raw는 최초 수집기 version·SHA-256과 함께 불변 보존한다. 향후 다시 수집할 때는 중복 key/page와 resume provenance를 보강한 patch 수집기로 `v1.2.1` 경로에 새 snapshot을 만들며 기존 경로를 덮어쓰지 않는다.

```bash
.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_collector_v1_1 collect \
  --source lunar \
  --output data/raw/saju_runtime/kasi/v1.1.0/lunisolar \
  --confirm-network COLLECT_KASI_RUNTIME_V1_1

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_term_coverage_collector_v1_2 collect \
  --output data/raw/saju_runtime/kasi/v1.2.1/solar-terms-api \
  --confirm-network COLLECT_KASI_TERM_COVERAGE_V1_2_1

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.kasi_almanac_1964_collector collect \
  --output data/raw/saju_runtime/kasi/v1.2.1/almanac-1964 \
  --confirm-network COLLECT_KASI_ALMANAC_1964_V1_0_1

.venv-data/bin/python -m scripts.evaluation.saju_runtime.kasi_official_solar_terms_collector collect \
  --output data/raw/saju_runtime/kasi/v1.3.0/official-solar-terms \
  --confirm-network COLLECT_KASI_OFFICIAL_SOLAR_TERMS_V1_0_0

.venv-data/bin/python -m scripts.evaluation.saju_runtime.iers_finals_collector collect \
  --output data/raw/saju_runtime/iers/v1.0.0/새-snapshot \
  --confirm-network COLLECT_IERS_FINALS2000A_V1_0_0
```

IERS 수집기는 HTTPS same-origin redirect, regular file·symlink, 0600 권한, 원문 SHA-256과 Skyfield parser coverage를 fail-closed로 검증한다. 자동 다운로드나 fallback은 허용하지 않으며 snapshot은 현재 UT1 민감도 진단에만 쓴다.

음양력 전수·OpenAPI 실제 coverage·공식 현재 계산 24기·1964년 역서·자문 표시 분을 함께 검증한다. 아래 명령은 보존된 raw snapshot과 Git 제외 DE440s로 부모 v8을 다시 계산한 뒤 현재 v9 chart-only 보고서를 재현한다. registry에 고정된 수집기 version·SHA-256과 일치해야 한다. strict/full Gate는 false를 유지하면서 chart-only Gate만 true여야 한다.

```bash
.venv-data/bin/python -m scripts.evaluation.saju_runtime.conformance_v9 validate-contract
.venv-data/bin/python -m scripts.evaluation.saju_runtime.conformance_v9 plan
.venv-data/bin/python -m scripts.evaluation.saju_runtime.conformance_v9 run \
  --kasi-lunar-snapshot data/raw/saju_runtime/kasi/v1.1.0/lunisolar/kasi_lunisolar.jsonl \
  --kasi-solar-term-snapshot data/raw/saju_runtime/kasi/v1.2.0/solar-terms-api/kasi_solar_terms.jsonl \
  --kasi-official-solar-term-snapshot data/raw/saju_runtime/kasi/v1.3.0/official-solar-terms/kasi_official_solar_terms.jsonl \
  --kasi-minute-snapshot data/raw/saju_runtime/kasi/v1.1.0/minute-references/kasi_minute_references.jsonl \
  --kasi-almanac-1964-snapshot data/raw/saju_runtime/kasi/v1.2.0/almanac-1964/kasi_almanac_1964_baengno.json \
  --iers-snapshot data/raw/saju_runtime/iers/v1.0.0/snapshot-2026-09-01-v3/finals2000A.all \
  --ephemeris "$(pwd -P)/data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"

.venv-data/bin/python -m scripts.evaluation.saju_runtime.conformance_v9 verify \
  --report-root data/reports/saju_runtime_conformance/v1.7.0/build-9f1784e74a4e

.venv-data/bin/python -m scripts.runtime.saju_runtime_v1_4 verify-release \
  --release-registry configs/runtime/calculation/releases/v1.4.0/release_registry.json

.venv-runtime/bin/python -m scripts.evaluation.saju_runtime.intake_fsm_gate run
```

과거 공식 근거 전용 진단 Gate는 고정 DE440s를 직접 읽어 12개 층화 120건을 재현한다. 공개 경로에는 case별 출생 입력·runtime ID·원시 응답을 쓰지 않고 집계와 build manifest만 둔다.

```bash
.venv-data/bin/python -m scripts.evaluation.saju_runtime.historical_candidate_gate validate-contract
.venv-data/bin/python -m scripts.evaluation.saju_runtime.historical_candidate_gate plan
.venv-data/bin/python -m scripts.evaluation.saju_runtime.historical_candidate_gate run \
  --ephemeris "$(pwd -P)/data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp"
.venv-data/bin/python -m scripts.evaluation.saju_runtime.historical_candidate_gate verify \
  --report-root data/reports/saju_historical_candidate/v1.0.0/build-5b80bfb2b7b9
```

진단 화면은 저장소 밖의 현재 사용자 소유 `0600` 32바이트 key를 받아 별도 port에서 실행한다. key 값·hash는 Git·문서·명령행 출력에 남기지 않으며 기존 8765 dashboard를 재시작하지 않는다.

```bash
.venv-data/bin/python scripts/training/phase5_dashboard.py serve-candidate \
  --ephemeris "$(pwd -P)/data/raw/saju_runtime/ephemeris/v1.1.0/de440s.bsp" \
  --id-key-file "/run/user/$(id -u)/saju-runtime-candidate-id-key" \
  --host 127.0.0.1 \
  --port 8766
```

v3.1 생성과 비학습 preflight 명령은 현재 실행하지 않는다. 기존 generator는 chart 4,350회와 period 900회를 같은 full release로 재생성하는 계약이며, chart-only v1.4 release를 그 입력으로 대체할 수 없다. 미래 full runtime release와 새 versioned generator 계약이 별도로 고정될 때 production HMAC key 수명주기·source build·split·preflight 명령을 함께 다시 발행한다.

기존 dashboard v1.8 canary는 v1.1 release 소비 코드이므로 v1.4 승인 근거로 재사용하지 않는다. v1.4 구조화 adapter와 암호화 persistence·보존 정책의 독립 dry-run 및 합성 local Gate에 이어 v1.9 production 통합 Gate `build-ea53c272c1d6`까지 완료했다. 현재 운영 process는 v1.9 전용 `--enable-chart-only-runtime`을 명시해 제한 활성화하며, v1.8의 `--enable-runtime-canary`는 사용하지 않는다.

## 14. 완료 기준

- [x] 단일 Korea-only profile과 지원 범위를 고정했다.
- [x] 버전·URL·wheel SHA-256·MIT 고지를 고정했다.
- [x] 기본 off와 explicit candidate mode를 분리했다.
- [x] 음양력·절입·4주·오행·지장간·십신·기간 core를 구현했다.
- [x] unknown/range와 DST fold/gap을 추측 없이 처리한다.
- [x] LLM-visible allowlist에서 내부 trace·ID를 숨긴다.
- [x] KASI 인증 수집기와 표시 분 수집기, 계층형 conformance v3를 구현했다.
- [x] KASI 표시 분 84건과 Skyfield/JPL 교차 비교 1,800건을 검증했다.
- [x] 엔진 간 날짜 차이를 공식 KASI 행 없이는 판정하지 않고 Gate에서 차단한다.
- [x] KASI 표시 분은 signed delta 진단과 분리해 KST 최근접 분 라벨 동등성으로 검증한다.
- [x] 1,800건 공개 JSONL·SVG와 동일 TT root 재투영 진단을 재현 가능하게 기록하고 ΔT 단일 원인 가설을 배제한다.
- [x] 5종 출생 파생 ID를 domain-separated HMAC-SHA256 v2로 교체했다.
- [x] 자유문 파서 없는 session v2.1/FSM v1.1과 앱 합성 Gate 100/100·구조/변조 check 18/18을 구현했다.
- [x] release registry와 승인 runtime의 hash chain·기본 off를 구현했다.
- [x] v3.0.1 20K를 읽기 전용 분석하고 v3.1 이관 수량을 고정했다.
- [x] v3.1 전수 재생성기와 새 split·비학습 preflight를 v1.2 release·HMAC 계약으로 고정했다.
- [x] 대시보드 v1.8에 구조화 chart·period·세션 snapshot canary를 기본 off로 구현했다.
- [x] KASI 54,787일 공식 snapshot을 확보하고 양음력·일진 mismatch 0을 확인한다.
- [x] KASI 24절기 API의 1900~2049 전 연도를 scan하고 실제 반환 범위 2000~2028년을 provider 보충 없이 고정한다.
- [x] KASI 1964년 역서 원문 백로 `9월 7일 24:00` 문서 사실과 현재 계산 `9월 7일 23:59`를 서로 다른 계산 시점의 근거로 보존한다.
- [x] Astronomy Engine과 Skyfield/DE440s를 1,800건 비교하고 어느 provider도 전체 적격 Gate를 통과하지 못함을 v5에 기록한다.
- [x] KASI 공식 현재 계산 다운로드 1920~2100의 원문 4,343행과 절입 2,172/2,172행을 확보하고 알려진 비절입 누락 1건을 고정한다.
- [x] 데이터 가용성과 provider 적격성 Gate를 분리하고 1900~1919 절입 240행을 `PROFILE_DETERMINISTIC`으로 분류한다.
- [x] OpenAPI 2011년 대한과 공식 현재 계산 다운로드의 날짜·분 충돌을 원 응답으로 재확인하고 비가용성으로 오판하지 않도록 기록한다.
- [x] 공식 현재 계산 1,560개 절입 분 라벨에서 Astronomy 303건·Skyfield 157건 mismatch로 둘 다 부적격임을 v6에 기록한다.
- [x] Skyfield root 수렴을 microsecond·황경 잔차로 검증하고 약 11초의 동일 TT 차이가 허용오차 때문이라는 가설을 배제한다.
- [x] Skyfield 같은 TT root의 proleptic UTC·내장 UT1·현재 IERS UT1·Astronomy/Espenak-Meeus UT 표시를 비교하고 내장 UT1을 우선 후보로 선정한다.
- [x] 공식 행을 snapshot 수집 시점의 과거 1,280행·미래 280행으로 분리하고 과거 1초 불확실성과 미래 지구 자전 예측 한계를 서로 다른 Gate에 반영한다.
- [x] 1964년 역서 `24:00`을 인쇄일 끝으로 정규화하는 규칙이 맞으며 현재 계산 날짜 mismatch의 원인이 아님을 검증한다.
- [x] provider 후보 Gate와 strict runtime Gate를 분리하고 후보 선정만으로 runtime·release·앱·데이터·학습 상태가 바뀌지 않도록 차단한다.
- [x] Skyfield 1.55·고정 DE440s·내장 UT1을 자동 다운로드와 Astronomy fallback 없는 v1.3 candidate runtime provider로 결합한다.
- [x] TT 경계 판정과 공식 표시 분을 분리하고 1900~1919 profile, snapshot 이전 과거, 이후 미래 권한을 구조화한다.
- [x] conformance v8에서 runtime↔별도 validator 1,800개 root와 전/정확/후 5,400건, 권한 240/1,280/280을 검증한다.
- [x] 기존 v1/v1.2 불변 구현 hash와 MIX20K-v3 runtime Gate를 유지한다.
- [x] 과거 공식 근거 전용 session v2.2/FSM v1.2에서 exact·range·unknown·fold ID를 재검산하고 profile·미래·기간 요청을 차단한다.
- [x] 기존 dashboard와 자산·process를 분리한 loopback·메모리 전용 후보 화면/API를 구현한다.
- [x] 실제 Skyfield/DE440s로 1964년 백로, 음력, 교정, stale call, 변조 HMAC, 공개 응답 경계를 포함한 12개 층화 120/120 Gate를 통과한다.
- [x] 정규화 양력 `1920-01-07~2026-08-31`의 과거 공식 원국만 허용하는 v1.4 chart-only wrapper와 source/Gate v1.7을 고정한다.
- [x] conformance v9에서 scope 328,722건, exact 77,908건, 경계 probe 2,558건, range/unknown 2,660건을 실패 0으로 검증한다.
- [x] 과거 원시 분 mismatch 14를 보존하고 ±1초 격리 분·range 차단·unknown 안정 50건을 각각 자동 Gate에 고정한다.
- [x] `saju-runtime-release-v1.4.0-63dc8d398e90`을 write-once로 생성하고 chart만 승인·period 차단·feature 기본 off를 검증한다.
- [x] 분리된 HMAC/AEAD key, AES-256-GCM persistence, 30분 보존·삭제·2-key rotation의 자동 dry-run 계약을 고정한다.
- [x] 구조화 event-only v1.4 adapter를 구현하고 실제 DE440s 합성 local canary 13개 층화 130/130을 통과한다.
- [ ] strict/full Runtime Gate와 미래·기간 범위의 profile 승인을 완료한다.
- [x] 실제 운영 key의 private provisioning·rotation·폐기와 retention 절차를 고정한다. 공개 앱 접근과 key 공개를 혼동하지 않는다.
- [x] 무인증 공개 권한, exact Origin·CSRF, rate limit·동시 process·로그 비노출을 포함한 dashboard v1.9 production binding을 구현한다.
- [ ] v3.1을 새 fingerprint로 생성하고 split/preflight를 재실행한다.
- [x] feature 기본 off 상태에서 합성 HTTP 100건과 실제 K0·KI20 1쌍의 production canary를 검증하고, 통과한 process만 제한 활성화한다.

## 진행 기록

- 2026-09-02
  - 작업 요약: 병합된 dashboard v1.9 구현으로 GPU production canary와 실제 공개 앱 통합 canary를 완료하고, 검증된 process만 chart-only로 제한 활성화했다.
  - 변경 범위: 공개 산출물은 `build-ea53c272c1d6`의 `aggregate.json`·`build_manifest.json` 두 파일뿐이다. 운영 key·원시 case·모델 원문·출생 입력·runtime 식별자·private path·공개 URL은 기록하지 않았다. 설정 기본 off, 기간 차단, strict/full runtime 차단을 유지했다.
  - 검증: production canary HTTP 100/100과 실제 K0·KI20 1쌍의 비어 있지 않은 출력·동일 snapshot을 통과했다. 같은 구현의 live process에서 상태 API 3종 200, 정상 원국, 절입 경계, 범위 밖, 변조 capability, 기간·legacy route, 잘못된 Origin을 검증했고 공개 HTTPS에서도 정상 원국과 보안 차단을 확인했다. 데스크톱·모바일 렌더링에 오류가 없었고, 운영 로그 110줄에 출생값·도시·24자리 capability·500이 없으며 session record는 0개다.
  - 남은 이슈·후속 작업: 이 완료는 limited chart-only 활성화만 허용한다. strict/full runtime·미래·기간, Phase 6 결정, MIX20K-v3.1, 추가 학습과 모델 승격은 계속 변경하지 않는다.

- 2026-09-02
  - 작업 요약: dashboard v1.9을 WSL2 transient `systemd` service로 재기동하는 과정에서 `nvidia-smi` 탐색 실패가 상태 API 500으로 전파되는 운영 회귀를 발견해 fail-soft 진단으로 교정했다.
  - 변경 범위: GPU snapshot은 실행 파일 부재·권한 오류·timeout을 `available=false`로 축소 응답하며 chart-only runtime 계산과 보안 경계는 그대로 유지한다. 운영 문서에 WSL2 `/usr/lib/wsl/lib` `PATH`와 native JIT용 Python header `CPATH` 전달 조건을 추가했다.
  - 검증: 새 CSRF session으로 live `/api/status`, `/api/model-checks`, `/api/runtime/status`가 모두 200이고 GPU가 감지됨을 확인했다. 누락 명령 회귀를 포함한 v1.9 표적 7건, 저장소 전체 unittest 553/553, Ruff, JavaScript syntax와 diff 검사를 통과했다.
  - 남은 이슈·후속 작업: 수정 병합 뒤 새 구현 fingerprint로 GPU production canary를 다시 생성·검증하고, 해당 build를 적재한 live process의 공개 API·로그·데스크톱/모바일 화면을 재검증한다.

- 2026-09-02
  - 작업 요약: PR #12 병합 후 전체 552건 회귀에서 과거 후보 Gate의 v1.8 `phase5_dashboard.py` byte hash 손상을 발견하고 v1.9 실행 진입점을 별도 파일로 분리했다.
  - 변경 범위: v1.8 진입점과 기존 dashboard 테스트를 원래 불변 바이트로 복원하고, production binding은 `phase5_dashboard_v1_9.py`로 이동했다. v1.9 canary·테스트·운영 문서만 새 진입점을 참조한다. 과거 보고서나 고정 hash를 새 코드에 맞춰 덮어쓰지 않았다.
  - 검증: v1.8 고정 SHA-256 `4a1679aa…093de`를 복원했고, 실패했던 historical candidate 3건을 포함한 관련 회귀 51건과 저장소 전체 unittest 552/552, Ruff, JavaScript syntax와 diff 검사를 통과했다.
  - 남은 이슈·후속 작업: 격리 수정이 전체 계약에 영향을 주지 않음을 확인했으므로 새 체크포인트를 병합한 뒤 GPU·live canary를 진행한다.

- 2026-09-02
  - 작업 요약: 승인 v1.4 원국 adapter를 dashboard v1.9의 공개 production 경계와 K0·KI20 공통 snapshot context에 결합했다. 설정 기본값은 off이며 실제 GPU·live canary 전에는 활성화하지 않는다.
  - 변경 범위: v1.8 config·asset은 불변 보존하고 v1.9 config·versioned UI, 구조화 session/event/delete API, AES-GCM persistence binding, single-process lease, 직렬화·rate limit, exact Host·Origin·CSRF, redacted 로그와 aggregate-only 100건+GPU 1쌍 canary 계약을 추가했다. 무인증 공개는 허용하지만 key 값·경로와 runtime capability는 공개하지 않는다. 기간·full runtime·Phase 6·sealed blind·MIX20K-v3.1·학습·모델 승격은 변경하지 않았다.
  - 검증: Ruff, JavaScript syntax, diff 검사와 dashboard v1.8 회귀를 포함한 표적 unittest 46건을 통과했다. 기존 operations, local canary `build-ddde6dce3d3c`, conformance v9 `build-9f1784e74a4e`, release v1.4를 재검증했고 실제 DE440s binding smoke와 합성 HTTP 100/100도 통과했다.
  - 남은 이슈·후속 작업: merge 뒤 기존 GPU dashboard를 안전하게 중지하고 실제 K0·KI20 1쌍 canary를 8767에서 실행한다. 모두 통과할 때만 동일 tunnel의 8765 process를 v1.9 명시 flag로 활성화하고 공개 smoke·로그 비노출·rollback 경로를 확인한다.

- 2026-09-02
  - 작업 요약: PR #10 병합 뒤 통합 정본을 다시 감사하고, v1.4 chart-only release의 운영 key·암호화 persistence·구조화 app adapter dry-run과 합성 local canary를 구현했다.
  - 변경 범위: `chart_only_security-v1.0.0`, adapter/canary Gate와 hash registry, `cryptography==50.0.1` 고정 의존성, 0600 분리 key loader·생성기, AES-256-GCM atomic session store, event-only adapter, CLI, 130-case canary와 aggregate-only 공개 build를 추가했다. 실제 운영 key·사용자 입력·case 출력은 만들거나 기록하지 않았고 기존 dashboard·모델 context·Phase 6·MIX20K-v3.1·학습 상태도 변경하지 않았다.
  - 검증: 기존 full project audit가 Phase 1 4종·Nemotron 1,000,000행·Phase 6 단회 완료·MIX20K-v3·grounded dialogue·conformance v8 byte identity를 sealed payload 비접근으로 통과했다. 이어 conformance v9·release, 관련 runtime/app 회귀 81건과 정본의 비자동 Gate 금지 검사 3건을 통과했다. Python 3.10 빈 `uv` 환경에 고정 12개 패키지를 설치해 `uv pip check`와 operations 계약·기본 off plan·공개 report verify를 재현했으며 전체 Ruff도 통과했다. adapter는 actual DE440s exact·범위 밖·절입 경계·교정 무효화·period 차단·공개 allowlist, 저장소는 key 분리·권한·변조 tag·retention·rotation을 검증했다. 최종 canary `build-ddde6dce3d3c`는 13개 층화 130/130, 실패 0이다.
  - 판단 교정: grounded dialogue의 `diagnostic_target_met=false`는 의미 품질 메타데이터 때문이 아니다. 최신 재채점에서 R1·R3는 통과했고 R0 prompt overflow 34건, K0 임의 네 기둥 3건·재질문 24%, model-narrow invalid 100건·exact 20%·false completion 1건이 자동 미달 원인이다. 별도 비자동 평가를 후속 Gate로 추가하지 않는다.
  - 남은 이슈·후속 작업: dry-run과 합성 canary는 production binding 승인이 아니다. 실제 secret manager·인증·권한·rate limit·동시 process 통합과 제한 production canary가 남았으며, strict/full runtime·period·v3.1·추가 학습·모델 승격은 계속 차단한다.

- 2026-09-02
  - 작업 요약: 정규화 양력 `1920-01-07~2026-08-31`의 과거 공식 원국만 승인하는 Skyfield runtime v1.4와 conformance v9를 구현하고, 통과 보고서에 결합된 chart-only release를 생성했다.
  - 변경 범위: runtime contract·output schema·profile v1.4, source/Gate v1.7, `ApprovedSajuRuntimeEngineV14`, CLI, conformance v9, write-once release 승인기와 schema, 회귀·실제 DE440s 통합 테스트, 공개 aggregate/build manifest와 runtime 정본 문서를 추가했다. exact는 `HARD_GT`, range·unknown은 ±1초 양끝의 공통 사실이 같을 때만 `POLICY_BOUND_RULE`이며 period는 항상 차단한다. 앱·dashboard·모델 context·MIX20K-v3.1·학습·Phase 6·sealed blind는 실행하거나 연결하지 않았다.
  - 검증: 부모 v8을 실제 KASI·IERS·DE440s 원본으로 재계산하고 scope 328,722건(허용 233,724·차단 94,998), 태양력·음력 exact 77,908건, 과거 절입 1,279행·probe 2,558건, range/unknown 2,660건을 실패 0으로 통과했다. 과거 원시 분 mismatch 14, 격리 분 50, 동일-분 range 차단 50, unknown 안정 50을 정확히 고정한 최종 보고서는 `build-9f1784e74a4e`, release는 `saju-runtime-release-v1.4.0-63dc8d398e90`이다. 전체 Ruff, v1.4·이전 runtime 회귀 64건, 실제 활성 release 경계 smoke, report/release 재검증, `uv pip check`, Phase 1 원천 4종·Nemotron 1,000,000행 검증을 통과했다. Git 제외 원천이 있는 clean `master` 기준 기존 전체 unittest도 527/527 통과했다. 격리 작업트리 전체 discover의 기존 1실패·22오류는 그 worktree에 Git 제외 원천·모델·과거 build가 없는 경로 문제로 분리했고 새 runtime 관련 실패는 없었다.
  - 남은 이슈·후속 작업: strict/full runtime Gate는 원시 분 mismatch 22와 미래 물리 순간 미판정으로 계속 false다. feature 기본 off와 `production_application_binding=false`를 유지하고, 운영 key 수명주기·암호화 persistence·앱 adapter·기간 승인 전에는 v3.1 생성·추가 학습·모델 승격을 진행하지 않는다.

- 2026-09-01
  - 작업 요약: 과거 공식 근거 전용 session v2.2/FSM v1.2를 별도 loopback 진단 화면과 실제 Skyfield v1.3 runtime에 연결하고, 12개 층화 120건 자동 Gate를 완료했다.
  - 변경 범위: 기존 8765 dashboard 자산과 process는 그대로 두고 `serve-candidate`, 127.0.0.1 전용 정적 화면·구조화 event API·최대 100개/30분 메모리 세션, versioned dashboard 계약과 공개 aggregate/build manifest를 추가했다. 브라우저·서버 disk 저장, 공개 session 조회, 외부 `chart_result`, 기간 계산과 모델 context 연결은 금지했다.
  - 검증: 실제 고정 DE440s로 exact·range·unknown·음력·1964년 백로 `23:59+09:00`·교정 무효화·stale call·변조 HMAC·1900~1919 profile·snapshot cutoff 이후·기간·공개 응답 각 10건, 총 120/120을 통과했다. 공개 보고서 `build-5b80bfb2b7b9`는 원시 출생값·내부 ID 없이 `diagnostic_target_met=true`이며 기존 dashboard 자산 hash 불변을 포함한다. 실제 CLI/HTTP smoke, Ruff, JavaScript 문법, `uv pip check`, 전체 unittest 527건과 Phase 1 1,000,000행·conformance v8 byte identity를 포함한 full 통합 audit도 통과했고 `sealed_blind_payload_opened=false`, GPU·학습·tracked write 모두 false였다.
  - 남은 이슈·후속 작업: 이 통과는 별도 로컬 진단 화면 binding만 허용한다. strict runtime Gate, profile ADR, release, production 앱, 모델 context, v3.1, 추가 학습과 모델 승격은 계속 차단한다.

- 2026-09-01
  - 작업 요약: runtime v1.3 결과 중 모든 절입 경계와 생시 대안이 `PAST_OFFICIAL_CORROBORATED`이고 가능한 출생시각 전체가 공식 snapshot cutoff 이전인 경우만 수용하는 session v2.2/FSM v1.2 계약을 구현했다.
  - 변경 범위: 기존 session v2.1/FSM v1.1을 보존하고 새 state schema·FSM·120건 Gate 계약·hash registry를 추가했다. exact 단일 결과는 `chart_id`, 범위·미상·fold 대안은 `chart_set_id`를 HMAC으로 재검산하며, 기간 요청은 `CANDIDATE_PERIOD_OUT_OF_SCOPE`로 고정 차단한다.
  - 검증: 1964년 백로 `23:59+09:00`, profile 구간, snapshot 직후, 복수 대안 중 권한 혼입, stale call, 변조 HMAC, cutoff 분, 교정 무효화와 공개 `chart_result` 차단을 포함한 새 11건과 기존 intake v1.1 10건을 통과했다. release·production 앱·context·v3.1·학습·승격 상태는 모두 false를 유지한다.
  - 남은 이슈·후속 작업: 기존 dashboard assets와 분리된 loopback 진단 화면·메모리 세션 API를 연결하고 실제 runtime을 사용하는 12개 층화 120건 Gate를 완료해야 한다. 기존 8765 서비스는 변경하거나 재시작하지 않는다.

- 2026-09-01
  - 작업 요약: runtime 보조 문서와 저장소 상위 정본을 Skyfield 1.55·DE440s·builtin UT1 기반 v1.3 candidate 및 conformance v8 최종 상태로 맞췄다. 과거 Astronomy Engine 경로는 이력·비교기로, 현재 후보 경로와 strict/release Gate는 별도 상태로 명시했다.
  - 변경 범위: 정본 버전을 `runtime-calculator-adoption-v2.6.2`로 올리고 profile ADR, 정책, 알려진 제한, 외부 conformance, 제3자 고지와 상위 상태 문서의 포인터를 갱신했다. runtime 구현·공식 snapshot·불변 conformance build는 수정하지 않았고 release registry, feature flag 기본값, 앱 연결, v3.1 생성과 학습도 수행하지 않았다.
  - 검증: KASI 음양력 54,787/54,787일, 공식 현재 계산 절입 2,172/2,172행과 runtime 대상 1,560행, Skyfield 날짜 mismatch 0·원시 분 mismatch 22, runtime↔별도 validator 1,800건과 경계 5,400건 mismatch 0인 기존 hash chain을 계약 테스트로 재검증했다. runtime v1.3/v8 연계 23건과 저장소 전체 unittest 429건, Ruff, Phase 1 원천 검증, `uv pip check`, 로컬 문서 링크, `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 과거 14건은 KASI 1초 불확실성 안이지만 미래 8건은 비승인 진단이며 strict Gate는 닫혀 있다. KASI 반올림·미래 지구 자전 불확실성의 자동 경계 판정과 운영 key·persistence 승인을 마치기 전까지 `runtime_approved=false`, release·앱·v3.1·학습 차단을 유지한다.

- 2026-08-31
  - 작업 요약: 루트 조사 초안을 현재 KI20·MIX20K-v3.0.1 상태에 맞는 Korea-only 실행 정본으로 축소·재작성하고, versioned 계약과 Python candidate runtime을 구현했다.
  - 변경 범위: `configs/runtime/calculation`, `scripts/runtime/calculation`, KASI 수집기, conformance v2, v3.1 읽기 전용 이관 분석기, 전용 requirements와 제3자 고지를 추가했다. 기존 tool/session schema, 데이터 build, checkpoint, sealed blind는 변경하지 않았다.
  - 검증: `uvx ruff check scripts tests`, runtime·conformance·v3 이관 unit 26건, JSON/manifest/hash chain 검증을 통과했다. `master` 병합 뒤 기존 로컬 산출물이 있는 환경에서 전체 `unittest` 312건도 41.058초에 모두 통과했다. 공개 KASI 지원 범위 63건의 음양력·일진 mismatch 0, 단일 profile 16/16, unknown/range 500, hash 200, 해외 20, host TZ·locale·DST·heuristic leak 검사도 통과했다. 최종 구현 hash를 반영한 보고서 `build-8db2f43d91ca`는 공식 전수 수량 부족으로 `runtime_gate_passed=false`다.
  - 남은 이슈·후속 작업: KASI service key 또는 검증된 공식 전체 snapshot과 12절 경계 자료가 필요하다. 확보 전 v3.1 생성, 앱 기본 활성화, 실제 학습을 수행하지 않는다.

- 2026-08-31
  - 작업 요약: R4~R7의 실행 코드를 v1.1 계층형 Gate로 구현했다. KASI 인증 API 수집, KASI 달력자료 84건 원문 수집, Skyfield/JPL 교차 검증, 승인 release, MIX20K-v3.1 전수 재생성·split·비학습 preflight, dashboard v1.8 runtime canary를 연결했다.
  - 변경 범위: `requirements-runtime-calculator-v1.1.txt`, runtime v1.1 계약·engine·CLI, conformance v3·collector·release, v3.1 generator/loader/preflight, dashboard config·server·assets·테스트, 제3자 고지와 이 정본을 갱신했다. 기존 v3.0.1 build, KI20 model/checkpoint, 실행 중 dashboard process, sealed blind payload는 변경하지 않았다.
  - 검증: KASI 공식기관 표시 HTML 7개를 Git 제외 경로에 수집해 84/84행을 원문 SHA-256과 재파싱으로 확인했다. Astronomy Engine 최대 차이 59.457159초, Skyfield/JPL 최대 차이 29.281515초로 두 60초 Gate를 통과했다. 1900~2049 Skyfield/JPL 절입 1,800건은 평균 절대 17.130844초, p99 57.591955초, 120초 초과·identity·순서 오류 0이고 profile 전/경계/후 5,400건 mismatch 0이다. `uvx ruff check scripts tests`, runtime·dashboard 표적 49건, `node --check`, `git diff --check`와 로컬 비추적 산출물이 있는 `master` 전체 `unittest` 327건(42.790초)을 통과했다. 최종 보고서 `build-2702394cde89`는 세 공식 인증 snapshot Gate만 false이며 release·v3.1·학습을 차단한다.
  - 남은 이슈·후속 작업: `/run/user/<UID>/saju-kasi-service-key`에 0600 KASI key가 필요하다. 54,787일과 3,600개 절기 날짜를 수집해 세 mismatch Gate가 0일 때만 release를 만들고 v3.1 생성→비학습 preflight→canary 순서로 진행한다. 실제 데이터 재생성·학습은 수행하지 않았다.

- 2026-08-31
  - 작업 요약: R4~R7 구현과 산출물의 경계 조건을 다시 감사해 release 보고서, v3.1 데이터 이관, dashboard runtime binding의 fail-closed 검증을 보강했다.
  - 변경 범위: conformance report의 canonical build ID·manifest governance·공식 snapshot·구현 파일 집합을 release 시 재검증한다. v3.0.1 원본 manifest SHA-256을 고정하고, v3.1 build preimage·artifact 집합·runtime release·5,250개 tool trajectory 수량을 preflight에서 다시 결합한다. dashboard는 runtime canary가 꺼진 상태에서 새 세션과 기존 결합 세션 모두 계산 사실을 모델 prompt에 넣지 못하게 했으며 내부 generation subprocess에도 명시 flag를 전달한다. 기존 데이터·모델·checkpoint·실행 중 dashboard process는 변경하지 않았다.
  - 검증: 변경 구현 hash로 새 conformance 보고서 `build-333036eb7024`를 생성했다. 이전 `build-2702394cde89`와 구현 hash를 제외한 집계 값이 byte-equivalent JSON 의미로 일치하며, KASI 표시 분 84건·Skyfield/JPL 절입 1,800건·경계 5,400건은 계속 통과한다. 회귀 표적 57건, Ruff, 계약·환경 검증, 실제 v3.0.1 manifest·20,000행 identity 재검증과 Git 제외 모델·원천·파생물을 포함한 저장소 전체 unittest 335건(43.545초)을 통과했다.
  - 남은 이슈·후속 작업: KASI 인증 snapshot 세 Gate는 계속 미충족이므로 `runtime_gate_passed=false`, release·v3.1 생성·학습 차단 상태를 유지한다.

- 2026-08-31
  - 작업 요약: 날짜 판정·분 표기·provider 격차 보고 결함을 v1.2 Gate로 교정하고, 출생 파생 ID HMAC v2와 자유문 파서 없는 구조화 intake FSM을 추가했다.
  - 변경 범위: 기존 v1·v1.1 산출물을 보존한 채 conformance v4·release v1.2·공개 1,800행 진단/산점도, 5종 HMAC ID, session v2/FSM·100건 앱 Gate를 새 버전으로 추가했다. 미래 MIX20K-v3.1 생성·preflight도 v1.2 release와 production key만 받도록 바꿨다. 기존 20K·모델·checkpoint·실행 중 dashboard·sealed blind는 변경하거나 실행하지 않았다.
  - 검증: 최종 코드 hash로 conformance `build-08ea29de9e94`와 FSM `build-571d0e82ee0e`를 재현했다. Skyfield/JPL 절입 1,800행·날짜 차이 1건/미판정 1건·runtime 최근접 분 mismatch 16건·Skyfield mismatch 0건·FSM 100/100을 확인했다. 차단 보고서의 release 승인과 release 없는 v3.1 생성이 모두 exit 2로 fail-closed했다. `uvx ruff check scripts tests`, runtime 계약·환경 검증, 전체 `unittest` 353건(38.957초), `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 1964년 백로의 공식 KASI 행과 공식 전수 snapshot이 없어 Runtime Gate는 차단 상태다. production key·암호화 persistence·보존 정책·앱 FSM adapter도 미승인이라 release·v3.1 생성·학습·dashboard 재기동을 수행하지 않는다.

- 2026-08-31
  - 작업 요약: v1.2 구현과 보고서를 다시 변조 관점에서 감사해 과거 session v2의 중복 `period` key, 보고된 Gate boolean 신뢰, 약한 FSM state 의미 검증과 stale tool 결과 수용 가능성을 교정했다.
  - 변경 범위: 과거 v2·FSM/Gate v1.0과 기존 보고서는 수정하지 않고 session v2.1·FSM/Gate v1.1·별도 intake hash registry를 추가했다. 활성 v1.2 JSON은 중복 key를 거부하고 release Gate를 집계에서 재계산한다. FSM은 slot·provenance·authority·현재 입력 fingerprint와 `scr2_` call ID를 검증한다. v3.1 preflight는 legacy/비정상 runtime ID를 거부한다. 기존 20K·모델·checkpoint·실행 중 dashboard·sealed blind는 변경하거나 실행하지 않았다.
  - 검증: JSON Schema draft 2020-12 자체 검증과 정상 state 전이 8단계, malformed JSON 변조 fuzz 예외 누출 0건, 표적 48건, Ruff 전체와 전체 `unittest` 365건(43.067초)을 통과했다. 최종 코드 hash로 conformance `build-ec510bc6922d`와 FSM `build-3366376bb01b`을 각각 두 번 실행해 같은 ID를 재현했고 artifact hash chain도 일치했다. release 승인·v3.1 생성·preflight는 release 부재로 모두 exit 2로 차단됐다.
  - 남은 이슈·후속 작업: Runtime Gate false 8개는 그대로다. KASI 54,787일·24절기 3,600건/12절 1,800건과 1964년 백로 공식 행을 확보하고, Astronomy Engine의 표시 분 mismatch 16/84를 해결할 provider 결정을 새 버전으로 검증해야 한다. 이후에만 production key 수명주기·암호화 persistence·실제 app adapter 통합 Gate, v3.1 생성·비학습 preflight 순으로 진행한다.

- 2026-08-31
  - 작업 요약: R4~R5 범위에서 KASI 인증 snapshot을 실제 수집하고, 1964년 백로를 KASI 디지털 역서로 판정한 뒤 Astronomy Engine·Skyfield/DE440s를 conformance v5에서 동등한 provider 후보로 비교했다.
  - 변경 범위: Git 제외 raw에 음양력 54,787일, 24절기 150년 coverage scan, 1964년 역서 JSON·원문 이미지·정규화 snapshot을 보존했다. 추적 범위에는 v1.3 source/Gate, 두 fail-closed 수집기, 고정 달력 bracket 기반 provider 비교, conformance v5 보고서 `build-ef1b8ddb527e`, 7개 회귀 테스트와 정책 문서를 추가했다. release 승인·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: KASI 음양력 54,787/54,787일의 양음력·일진 mismatch 0, 24절기 API 1900~2049 scan 150/150년과 실제 반환 2000~2028년 696건, 1964년 역서 `9월 7일 24:00` 원문·이미지 hash chain을 확인했다. 두 provider 1,800건은 평균 절대 17.130844초, p99 57.566090초, 최대 80.666231초이며 120초 초과·identity·순서 오류 0이다. Astronomy는 1964 공식 civil date를 통과하나 분 표기 16/84가 다르고, Skyfield는 분 표기 84/84를 통과하나 1964 공식 civil date가 달라 선택하지 않았다. v5를 두 번 실행해 같은 `build-ef1b8ddb527e`를 재현했다. `uvx ruff check scripts tests`, 전체 `unittest` 372건(46.209초), v1.2 계약·validator 환경, `uv pip check`, Phase 1 source 계약·원본 verify, `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: 공식 24절기 Gate는 696/3,600, 12절은 348/1,800으로 미달이며 선택 가능한 provider도 없다. `technical_gate_passed=false`, `runtime_approved=false`, `release_approval_performed=false`와 candidate ADR을 유지한다. 공식 coverage 확장 또는 새 provider·정당화된 경계 정책 없이는 release·앱·v3.1·학습으로 진행하지 않는다.

- 2026-08-31
  - 작업 요약: 구현된 R4~R5 수집·판정·provider 비교·conformance를 fault injection과 별도 수치 재계산으로 디버깅해 중복 JSON key, 중복 역서 page, 약한 resume manifest 검증을 교정했다.
  - 변경 범위: 기존 `v1.2.0` raw와 v5.0.0 보고서는 수정하지 않았다. 향후 수집기는 `v1.2.1` raw 경로를 쓰도록 patch version을 올리고, 공통 strict JSON loader·canonical byte·원 수집기 hash·Gate parent hash chain·0600/O_NOFOLLOW 재검증을 v5.0.1에 추가했다. 새 v1.3.1 source/Gate와 보고서 `build-1e754de17c82`, 회귀 테스트 14건을 추가했다. release 승인·runtime provider 변경·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: KASI 역서 원본 이미지를 육안 재확인했고, 기존 raw를 원 응답부터 다시 파싱해 1964년 백로 `9월 7일 24:00` 정규화와 696개 API 행을 재검증했다. Astronomy Engine·Skyfield 1,800행은 이전 보고서와 0초 차이로 재현됐고 Skyfield 근의 최대 황경 잔차는 약 `4.3e-8` 각초였다. v5.0.1을 두 번 실행해 같은 `build-1e754de17c82`를 얻었으며 v5.0.0과 Gate·provider·공식 근거 결과가 같고 artifact·implementation hash chain도 일치한다. `uvx ruff check scripts tests`, 전체 `unittest` 379건(44.454초), v1.2 계약·validator 환경, `uv pip check`, Phase 1 source 계약·원본 verify를 통과했다.
  - 남은 이슈·후속 작업: 입력 검증 결함은 닫혔지만 공식 coverage와 provider 적격성은 달라지지 않았다. 공식 24절기 696/3,600, 12절 348/1,800과 선택 가능한 provider 없음 때문에 `technical_gate_passed=false`, `runtime_approved=false`, `release_approval_performed=false`를 유지한다.

- 2026-09-01
  - 작업 요약: KASI 공식 현재 계산 24기 다운로드를 새 hard evidence로 수집하고, 데이터 가용성과 provider 적격성을 v1.4 Gate에서 분리했다. 원 UTC 격차를 동일 TT root로 재투영해 ΔT 단일 원인 가설을 검증했으며, 과거 역서와 비공식 달력자료는 현재 provider 판정에서 advisory로 분리했다.
  - 변경 범위: Git 제외 `v1.3.0/official-solar-terms`에 원문·4,343행 정규화 snapshot·manifest를 0600으로 보존했다. 추적 범위에는 v1.4 source/Gate, fail-closed 공식 다운로드 수집기, provider 비교 v2, conformance v6, 두 SVG와 1,800행 공개 진단, 회귀 테스트 12건, 정본 갱신을 추가했다. release 승인·runtime provider 변경·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: 원문 4,343/4,344행의 유일한 누락이 비절입인 2030년 우수이며 절입은 2,172/2,172임을 확인했다. OpenAPI 696행은 모두 매핑됐고 2011년 대한의 source 간 충돌 1건을 라이브 원 응답으로 재확인했다. 공식 현재 계산 1,560개 절입에서 Astronomy Engine은 분 303건·날짜 1건, Skyfield는 분 157건·날짜 0건 mismatch라 둘 다 부적격이다. 동일 TT root에서도 평균 절대 11.513609초와 profile 분 라벨 차이 330건이 남는다. v6를 두 번 실행해 같은 `build-3366c5069a26`을 재현했고 데이터 가용성·baseline은 통과, provider 적격성·technical Gate는 실패했다. `uvx ruff check scripts tests`, v6 표적 12건, 전체 `unittest` 391건(44.780초), v1.2 계약·validator 환경, `uv pip check`, Phase 1 source 계약·원본 verify를 통과했다.
  - 남은 이슈·후속 작업: 가용 공식 데이터 부족은 해소됐지만 전체 hard 분 라벨을 통과하는 provider가 없다. 새 provider 또는 물리적으로 정당화된 판정 정책이 별도 승인되기 전까지 `runtime_approved=false`, `release_approval_performed=false`를 유지하고 release·앱·v3.1·학습을 진행하지 않는다.

- 2026-09-01
  - 작업 요약: 공식 1,560행 확대 뒤의 provider 정확도 가설을 전수 재검증하고, 같은 Skyfield/DE440s TT root에 네 시간 표시 모델을 적용해 Skyfield 1.55 내장 UT1을 우선 후보로 선정했다. 후보 선정과 strict runtime 승인을 v1.5 Gate로 분리했다.
  - 변경 범위: Git 제외 `data/raw/saju_runtime/iers/v1.0.0/snapshot-2026-09-01-v3`에 IERS `finals2000A.all` 19,969행을 0600 불변 snapshot으로 보존했다. 추적 범위에는 fail-closed IERS 수집기, provider 비교 v3, v1.5 source/Gate, conformance v7 보고서 `build-01111af7e09c`, 공개 1,800행·SVG 진단, 회귀 테스트 15건과 이 정본 갱신을 추가했다. IERS 비교는 원문을 직접 파싱해 자동 다운로드·fallback을 제거했고 snapshot 범위 밖 외삽을 진단 전용으로 명시했다. release 승인·runtime provider 변경·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: Astronomy 분 mismatch 303·날짜 1, Skyfield proleptic UTC 157·날짜 0을 재현했다. Skyfield의 Astronomy ΔT UT 97, 현재 IERS 기반 UT1 29, 내장 UT1 22·날짜 0으로 줄었고 내장 UT1의 과거 1,280행 mismatch 14건은 모두 공식 1초 불확실성 범위 안이며 미래 280행 mismatch 8건은 비승인 예측 진단으로 분리했다. Skyfield root 최대 황경 잔차 `4.2564e-8` 각초와 32↔48회 최대 `101.09µs` 차이로 약 11초 격차의 근찾기 수렴 원인 가설을 배제했다. 1964년 `24:00`의 다음 날 정규화가 맞고 현재 계산 날짜 mismatch 원인이 아님을 확인했다. v7을 두 번 실행해 같은 `build-01111af7e09c`을 재현했으며 표적 v7 18건, 이전 v5/v6 26건, 전체 `unittest` 409건(35.731초), Ruff·format, v1.2 계약·validator 환경, `uv pip check`, Phase 1 source 계약·원본 verify, `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: `provider_candidate_gate_passed=true`지만 원시 분 라벨 mismatch 22와 미래 물리 순간 미판정 때문에 `strict_runtime_provider_gate_passed=false`, `runtime_approved=false`, `release_approval_performed=false`다. KASI 반올림 규약·미래 ΔT의 자동 판정 계약으로 strict Gate를 충족하기 전에는 release·앱·v3.1·학습으로 진행하지 않는다.

- 2026-09-01
  - 작업 요약: Skyfield 1.55·고정 DE440s·내장 UT1을 v1.3 candidate runtime provider로 구현하고, TT 경계 판정과 공식 표시 분을 분리했다. 1900~1919 profile·snapshot 이전 과거·이후 미래 권한을 구조화해 원국·생시 후보·기간 결과에 노출하고 conformance v8으로 실제 runtime binding을 검증했다.
  - 변경 범위: v1.3 runtime contract·output schema·profile·requirements와 v1.6 source/Gate, Skyfield provider·절입 권한 type·v1.3 전용 계산 adapter·HMAC v2 wrapper, conformance v8 및 공개 보고서 `build-a49aed186743`, 회귀 테스트를 추가했다. 기존 v1/v1.2 구현 네 파일은 불변 Gate hash로 복원·보존했다. Git 제외 DE440s 외에는 raw를 만들거나 수정하지 않았고 release 승인·production provider 연결·앱 연결·MIX20K-v3.1 생성·preflight·학습·dashboard 재기동은 수행하지 않았다.
  - 검증: runtime과 별도 validator의 1,800개 TT root 최대·평균 차이 `0.0µs`, UTC·UT1 표시 분 mismatch 0, 전·정확·후 5,400건 배정 실패 0을 확인했다. 권한은 profile 240·과거 공식 corroborated 1,280·미래 forecast nonapproval 280으로 정확히 분리됐다. v8을 두 번 실행해 같은 `build-a49aed186743`을 재현했고 표적 19건, 전체 `unittest` 424건(39.749초), `uvx ruff check scripts tests`, `uv pip check`, Phase 1 source 계약·원본 verify, `git diff --check`를 통과했다.
  - 남은 이슈·후속 작업: candidate runtime conformance는 통과했지만 공식 원시 분 라벨 mismatch 22건(과거 14·미래 8)과 미래 물리 순간 미판정 때문에 `strict_runtime_provider_gate_passed=false`, `runtime_gate_passed=false`, `runtime_approved=false`다. release·앱·v3.1·학습은 별도 승인 전까지 차단한다.

- 2026-09-01
  - 작업 요약: v1.3 candidate runtime과 conformance v8을 변조·오류 경계에서 다시 감사해 절입 증거의 provider 결속과 구조 검증을 fail-closed로 보강했다.
  - 변경 범위: 다른 provider 또는 provider 계산값과 다른 절입 경계, 빈·비정규 role, 변조된 summary/context, JSON의 `0`과 `false` 혼동을 거부한다. 비정상 연도·절기 index·timezone 타입도 도메인 오류로 닫고, provider 종료 뒤 기간 계산이 예외 대신 blocked 결과를 반환하게 했다. 현재 구현 hash와 맞는 v8 보고서만 선택하도록 회귀 테스트를 고쳤으며, DE440s 절대경로 계약과 충돌하던 정본 재현 명령도 바로잡았다. 최종 코드와 대응하는 `build-8bd88d6db03a`만 새 write-once 이력으로 보존했다.
  - 검증: 최종 fault injection 27종에서 비정상 입력 수용·예외 누출 0건, v1.3 표적 16건과 v8 artifact 3건을 통과했다. 최종 v8을 두 번 실행해 같은 `build-8bd88d6db03a`를 재현했고 최초 v8과 구현 hash 세 파일 외의 집계가 같았다. 1,800개 TT root의 최대·평균 차이 `0.0µs`, UTC·표시 분 mismatch 0, 5,400개 경계 실패 0, 권한 240/1,280/280을 유지했다. 전체 `unittest` 428건(40.028초), `uvx ruff check scripts tests`, `uv pip check`, Phase 1 source 계약·1,000,000행 원본 verify, 보고서 manifest·민감 경로 검사와 `git diff --check`를 통과했다. 기존 v1/v1.2 구현 네 파일은 HEAD·과거 보고서 hash와 동일하다.
  - 남은 이슈·후속 작업: 추가 진단용 최신 Ruff formatter는 저장소 기존 파일 다수의 포맷 차이를 보고해 이번 범위에서 일괄 수정하지 않았다. 기능 Gate는 그대로이며 원시 분 라벨 mismatch 22건과 미래 물리 순간 미판정 때문에 strict runtime Gate·release·앱·MIX20K-v3.1·학습은 계속 차단한다.
