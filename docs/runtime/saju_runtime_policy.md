<!-- saju_runtime_policy.md - 앱·평가 코드가 따라야 할 만세력 runtime 사용 규칙을 요약한다. -->

# 사주 Runtime 사용 정책

현재 제한 승인 경로는 Skyfield 1.55·고정 JPL DE440s·내장 UT1을 사용한 `saju-runtime-python-v1.5.0` 원국+단일 일진 release다. 부모 v1.4 원국 범위와 conformance v9를 보존하고, conformance v10.0.0 `build-46185262164f`와 release `saju-runtime-release-v1.5.0-8b1d6ea2d46e`에 단일 날짜 8,522일을 결합했다. feature는 기본 off다. dashboard v1.11의 명시적 원국·날짜 binding은 actual HTTP·GPU canary와 master `737846c` 운영 전환 후 공개 HTTPS·실제 KI20 live canary까지 통과해 명시 flag로 제한 활성화했다. strict/full runtime Gate, 주·월·연 기간과 데이터·모델 승격 권한은 열지 않았다. 기존 v1~v1.4 계약과 보고서는 불변 이력으로 보존한다.

- 기본 `ApprovedSajuRuntimeEngineV15()`은 release가 없으면 `RUNTIME_RELEASE_REQUIRED`, feature가 꺼져 있으면 `RUNTIME_FEATURE_DISABLED`를 반환한다.
- 활성화에는 고정 release 경로, 고정 DE440s 절대경로, 현재 사용자 소유 0600 32바이트 production key와 명시적 `enable_approved_runtime=True`가 모두 필요하다. 자동 ephemeris 다운로드, Astronomy fallback, 상대경로 추측은 금지한다.
- 원국 승인 범위는 대한민국·`Asia/Seoul`·정규화 양력 `1920-01-07~2026-08-31`이다. 단일 일진은 같은 process에서 exact `HARD_GT` 원국을 계산한 뒤 서버 KST 오늘과 `2026-09-02` 중 늦은 날부터 `2049-12-31`까지만 허용한다.
- 오전/오후는 range로 유지하고 대표시각을 만들지 않는다.
- 생시 미상은 시주를 `null`로 두고 후보의 공통 사실만 사용한다.
- DST gap을 이동하거나 fold를 임의 선택하지 않는다.
- 모델에는 `status`, `hard_facts`, `fact_authority`, `code`, `message`, `limitations`만 보낸다.
- exact 단일 원국만 `HARD_GT`, range·unknown 공통 사실만 `POLICY_BOUND_RULE`이다. v1.3 `HARD_CANDIDATE`를 승인된 hard fact로 표현하지 않는다.
- `hard_facts.solar_term_evidence`의 provider·TT/UTC·표시 분·권한을 결과와 함께 보존한다. provider가 만들지 않은 경계, 다른 provider 경계, 변조된 권한 집계는 거부한다.
- 절입 권한은 1900~1919년 `PROFILE_DETERMINISTIC`, snapshot 수집시점까지 과거 `PAST_OFFICIAL_CORROBORATED`, 이후 미래 `FORECAST_DIAGNOSTIC_NONAPPROVAL`로 분리한다. provider 생성값 자체를 공식 원문으로 표시하지 않고 미래 값을 `HARD_GT`로 승격하지 않는다.
- v1.4는 모든 절입 경계가 `PAST_OFFICIAL_CORROBORATED`·`SOURCE_HARD_FACT`인 결과만 승격한다. 공식 root ±1초가 입력 minute와 겹치는 exact는 차단하고, range·unknown은 -1초·기준·+1초의 공통 사실이 같을 때만 허용한다.
- `calculate_saju_period`는 `period_type=day`, 같은 시작·종료 날짜, `Asia/Seoul`, KST `12:00` 평가만 허용한다. 주·월·연, 원국과의 관계, 대운·세운·사건 예측은 차단한다. 반환하는 미래 날짜 label은 공식 전수 대조 결과이며 미래 절입 물리 순간을 확정했다는 뜻이 아니다.
- 신강약·격국·용신·대운·자동 해석은 runtime fact payload에 넣지 않는다.
- 새 출생 파생 ID는 32바이트 0600 key 파일의 domain-separated HMAC-SHA256과 v2 prefix를 사용한다. production key가 없으면 fail-closed하며, 고정 test signer는 conformance에만 쓴다.
- app adapter는 HMAC key와 별도의 32바이트 0600 AEAD key를 요구한다. 같은 inode·hardlink·key material, symlink 경로, 다른 소유자·mode를 거부한다.
- session state는 AES-256-GCM과 write별 12바이트 OS 난수 nonce로만 0700 root의 0600 record에 저장한다. 최대 100 session·1,800초 보존을 적용하고 ciphertext 변조는 인증 tag 실패로 차단한다.
- HMAC key rotation은 기존 session identity를 무효화하고 재계산한다. AEAD key rotation은 active+이전 key 최대 2개만 허용하고 old record를 읽은 직후 active key로 재암호화한다. 물리적 secure overwrite는 주장하지 않는다.
- KASI 공식 현재 계산의 분 표기는 원문 고지에 따라 최근접 분으로 비교한다. 과거에는 공개 ±30초 구간에 KASI가 밝힌 1초 불확실성을 더한 범위를 candidate 판정에만 쓰며, strict Gate에는 이 완화를 적용하지 않는다.
- KASI 역서의 `24시 00분` 표기는 다음 civil day의 `00:00`으로 정규화하되, 해당 원문 정밀도보다 작은 초 단위 정확도를 주장하지 않는다.
- 1964년 백로의 현재 계산 `9월 7일 23:59`와 과거 역서 `9월 7일 24:00`은 서로 다른 vintage로 보존한다. 과거 역서를 현재 provider 순간의 hard block으로 사용하지 않는다.
- OpenAPI가 0건을 반환한 연도는 provider 값으로 채우지 않는다. 1920~2100년 공식 현재 계산의 절입 2,172/2,172행을 별도 hard evidence로 쓰고, 1900~1919년 240행은 `PROFILE_DETERMINISTIC`으로 분리한다.
- provider 후보 선정, chart-only 승인, strict/full runtime 승인을 분리한다. Skyfield 후보는 과거 공식 chart-only Gate를 통과했지만 원시 분 라벨 mismatch 22와 미래 물리 순간 미판정 때문에 strict/full release 대상은 아니다.
- 120초 provider 교차 차이 기준은 비권위 회귀 가드다.
- 기존 일반 intake 계약은 `intake_registry-v1.1.0.json`이 고정한 session v2.1·FSM v1.1·Gate v1.1이다. dashboard v1.11은 별도 `saju-chart-day-session-state-v1.1`·event-only FSM을 사용하고 자유문 날짜 parser를 두지 않는다. 중복 `period` key가 있는 과거 session v2는 이력 보존용이며 앱에서 읽지 않는다.
- 구조화 intake FSM은 자유문을 파싱하지 않는다. 앱 계층이 검증된 event를 넘기며 release·production key·FSM 100/100·저장 암호화·보존 정책이 모두 준비돼야 계산 action을 낸다.
- chart·period tool 결과는 FSM이 발급한 현재 `scr2_` HMAC call ID를 그대로 되돌려야 한다. 이전 요청 결과, 렌더링 완료 결과, 현재 slot과 다른 `sif2_` fingerprint가 있는 cache는 거부한다.
- 활성 v1.5 계약·보고서·release는 모든 중첩 수준의 중복 JSON key를 거부한다. release validator는 보고서의 요약 boolean을 신뢰하지 않고 부모 v9·8,522일 공식 matrix·runtime positive/negative·governance 집계에서 Gate를 다시 계산한다.
- 현재 고정 release 경로는 `configs/runtime/calculation/releases/v1.5.0/release_registry.json`이며 부모 v1.4 release, report·manifest·구현·공식 snapshot SHA-256이 모두 같아야 한다.
- 운영 dry-run 계약은 `configs/runtime/operations/registry-v1.0.0.json`, 공개 canary는 `data/reports/saju_runtime_app_canary/v1.0.0/build-ddde6dce3d3c/`로 고정한다. aggregate에는 case 입력·응답·runtime ID·key·ciphertext·private path를 넣지 않는다.
- 미래 MIX20K-v3.1 생성·preflight는 chart와 기존 900개 period 전체를 승인한 별도 full release가 필요하다. 단일 `day`만 추가한 v1.5를 입력으로 대체하지 않으며 현재 생성·preflight·학습은 모두 미실행이다.

정본과 Gate는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md)를 따른다.
