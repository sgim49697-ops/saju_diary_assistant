<!-- saju_runtime_policy.md - 앱·평가 코드가 따라야 할 만세력 runtime 사용 규칙을 요약한다. -->

# 사주 Runtime 사용 정책

현재 활성 후보는 Skyfield 1.55·고정 JPL DE440s·내장 UT1을 TT 절입 경계에 결합한 `saju-runtime-python-v1.3.0`이다. conformance v8.0.0 `build-8bd88d6db03a`의 candidate runtime 결합 Gate는 통과했지만 strict Gate·release·production 승인은 통과하지 않았다. 기존 v1·v1.1·v1.2 계약과 보고서는 불변 이력으로 보존한다.

- 기본 `SajuRuntimeEngineV13()`은 `RUNTIME_FEATURE_DISABLED`를 반환한다.
- 로컬 conformance·개발 검증만 test signer, 고정 DE440s 절대경로와 `enable_candidate_runtime=True`를 명시한다. 자동 ephemeris 다운로드, Astronomy fallback, 상대경로 추측은 금지한다.
- 지원 범위는 대한민국·`Asia/Seoul`·1900~2049다.
- 오전/오후는 range로 유지하고 대표시각을 만들지 않는다.
- 생시 미상은 시주를 `null`로 두고 후보의 공통 사실만 사용한다.
- DST gap을 이동하거나 fold를 임의 선택하지 않는다.
- 모델에는 `status`, `hard_facts`, `fact_authority`, `code`, `message`, `limitations`만 보낸다.
- `HARD_CANDIDATE`를 학습 Gold나 전문가 인증 사실로 표현하지 않는다.
- `hard_facts.solar_term_evidence`의 provider·TT/UTC·표시 분·권한을 결과와 함께 보존한다. provider가 만들지 않은 경계, 다른 provider 경계, 변조된 권한 집계는 거부한다.
- 절입 권한은 1900~1919년 `PROFILE_DETERMINISTIC`, snapshot 수집시점까지 과거 `PAST_OFFICIAL_CORROBORATED`, 이후 미래 `FORECAST_DIAGNOSTIC_NONAPPROVAL`로 분리한다. provider 생성값 자체를 공식 원문으로 표시하지 않고 미래 값을 `HARD_GT`로 승격하지 않는다.
- 신강약·격국·용신·대운·자동 해석은 runtime fact payload에 넣지 않는다.
- 새 출생 파생 ID는 32바이트 0600 key 파일의 domain-separated HMAC-SHA256과 v2 prefix를 사용한다. production key가 없으면 fail-closed하며, 고정 test signer는 conformance에만 쓴다.
- KASI 공식 현재 계산의 분 표기는 원문 고지에 따라 최근접 분으로 비교한다. 과거에는 공개 ±30초 구간에 KASI가 밝힌 1초 불확실성을 더한 범위를 candidate 판정에만 쓰며, strict Gate에는 이 완화를 적용하지 않는다.
- KASI 역서의 `24시 00분` 표기는 다음 civil day의 `00:00`으로 정규화하되, 해당 원문 정밀도보다 작은 초 단위 정확도를 주장하지 않는다.
- 1964년 백로의 현재 계산 `9월 7일 23:59`와 과거 역서 `9월 7일 24:00`은 서로 다른 vintage로 보존한다. 과거 역서를 현재 provider 순간의 hard block으로 사용하지 않는다.
- OpenAPI가 0건을 반환한 연도는 provider 값으로 채우지 않는다. 1920~2100년 공식 현재 계산의 절입 2,172/2,172행을 별도 hard evidence로 쓰고, 1900~1919년 240행은 `PROFILE_DETERMINISTIC`으로 분리한다.
- provider 후보 선정과 strict runtime 승인을 분리한다. Skyfield 후보는 날짜 0건·과거 허용범위·경계 검사를 통과했지만 원시 분 라벨 mismatch 22와 미래 물리 순간 미판정 때문에 release 대상이 아니다.
- 120초 독립 엔진 차이 기준은 비권위 회귀 가드다.
- 활성 intake 계약은 `intake_registry-v1.1.0.json`이 고정한 session v2.1·FSM v1.1·Gate v1.1이다. 중복 `period` key가 있는 과거 session v2는 이력 보존용이며 앱에서 읽지 않는다.
- 구조화 intake FSM은 자유문을 파싱하지 않는다. 앱 계층이 검증된 event를 넘기며 release·production key·FSM 100/100·저장 암호화·보존 정책이 모두 준비돼야 계산 action을 낸다.
- chart·period tool 결과는 FSM이 발급한 현재 `scr2_` HMAC call ID를 그대로 되돌려야 한다. 이전 요청 결과, 렌더링 완료 결과, 현재 slot과 다른 `sif2_` fingerprint가 있는 cache는 거부한다.
- 활성 후보 v1.3 계약·보고서와 과거 v1.2 release 경로는 모든 중첩 수준의 중복 JSON key를 거부한다. 향후 release 승인기는 보고서의 요약 boolean을 신뢰하지 않고 집계 metric에서 다시 계산해야 한다.
- 현재 v1.3에는 승인 wrapper나 release registry가 없다. 과거 v1.2 release 소비 계약을 v1.3 승인으로 재사용하지 않는다.
- 미래 MIX20K-v3.1 생성·preflight는 승인된 runtime release ID·HMAC ID 계약·새 fact source를 전수 검증해야 하며 release/key 전에는 source dataset을 읽지 않는다. 현재 v3.1 생성·preflight·학습은 모두 미실행이다.

정본과 Gate는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md)를 따른다.
