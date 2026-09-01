<!-- default_profile_adr.md - 첫 한국 runtime profile의 chart-only 승인과 full production 보류 경계를 기록한다. -->

# ADR: `KR_CIVIL_MIDNIGHT_V1`

- 상태: 과거 공식 chart-only release 승인, strict/full runtime·production 앱 연결 보류
- 결정일: 2026-09-02
- 활성 release 계약: `saju-runtime-python-v1.4.0`
- 최신 검증: conformance v9.0.0 `build-9f1784e74a4e`

첫 한국 profile은 역사 civil time, 입춘 연 경계, 12절 월 경계, 00:00 일 경계, 민간시 시주, 진태양시 미적용, 지장간 본기 기준 지지 십신으로 고정한다. 절입 경계 비교는 TT, 공식 표시 분 비교는 `UT1_NOMINAL_PLUS_FIXED_KST`로 분리한다.

이 선택은 제품의 최초 범위를 명확하게 만들고 서로 다른 학파 축이 한 결과에 섞이는 것을 막는다. 다른 일 경계나 진태양시가 필요하면 기존 profile을 수정하지 않고 새 policy ID를 만든다.

## 근거와 provider 결정

- KASI 음양력 1900~2049년 54,787일은 양음력·일진 mismatch 0으로 전수 확보했다.
- KASI 24절기 OpenAPI는 150년 전수 scan에서 2000~2028년 696건만 반환했다. 이 반환 한계를 공식 데이터 부재 전체로 해석하지 않고, KASI 공식 현재 계산 다운로드를 별도 수집했다.
- 공식 현재 계산 원문은 1920~2100년 4,343/4,344행이며 유일한 누락은 비절입인 2030년 우수다. 연·월 경계에 쓰는 절입은 2,172/2,172행, runtime 범위에서는 1,560행으로 완전하다.
- 1900~1919년 절입 240행은 공식 snapshot에 생성값을 써넣지 않고 `PROFILE_DETERMINISTIC`으로 분리한다.
- Skyfield 1.55·고정 JPL DE440s·내장 UT1은 공식 1,560행에서 날짜 mismatch 0, 원시 분 라벨 mismatch 22다. snapshot 수집 시점까지의 과거 1,280행 중 14건은 KASI가 밝힌 1초 불확실성 범위 안이고, 미래 280행 중 8건은 승인 근거가 아닌 예측 진단이다.
- Astronomy Engine은 같은 공식 행에서 분 mismatch 303, 날짜 mismatch 1이므로 현재 우선 후보가 아니다. 다만 provider 교차 비교와 과거 v1.2 경로의 회귀 검증용 의존성으로 보존한다.

1964년 백로는 서로 다른 KASI 원천을 하나로 덮어쓰지 않는다. 공식 현재 계산은 `1964-09-07 23:59 KST`이고 Skyfield 내장 UT1 표시와 일치한다. 디지털 역서는 `9월 7일 24:00`이며 다음 civil day `1964-09-08 00:00`으로 정규화되고 Astronomy Engine 순간과 일치한다. 이는 정규화 오류가 아니라 서로 다른 vintage의 문서 사실이며, 과거 역서는 현재 물리 provider를 hard block하는 원천으로 사용하지 않는다.

## 승인 경계

v1.3 candidate runtime은 Skyfield provider를 실제 계산 경로에 결합한다. 1,800개 TT root·UTC·표시 분은 별도 validator와 mismatch 0이고, 전·정확·후 5,400개 경계 배정도 mismatch 0이다. 결과 권한은 다음처럼 구조화한다.

- 1900~1919: `PROFILE_DETERMINISTIC` 240행
- 공식 snapshot 수집시점까지의 과거: `PAST_OFFICIAL_CORROBORATED` 1,280행
- 이후 미래: `FORECAST_DIAGNOSTIC_NONAPPROVAL` 280행

이 통과는 candidate 계산 경로의 일관성을 뜻한다. 원시 분 라벨 mismatch 22와 미래 물리 순간 미판정 때문에 strict/full runtime provider Gate는 계속 실패 상태다.

v1.4는 이 profile을 정규화 양력 `1920-01-07~2026-08-31`의 과거 공식 원국에만 제한해 승인한다. conformance v9는 scope matrix 328,722건, 태양력·음력 exact 77,908건, 과거 절입 경계 probe 2,558건, range/unknown 2,660건을 실패 0으로 검증했다. KASI 과거 원시 분 mismatch 14건은 그대로 보존하고, ±1초 안에 minute 격자와 겹치는 50개 분은 exact와 불안정 range에서 차단한다. unknown은 ±1초 양끝의 공통 사실이 같을 때만 `POLICY_BOUND_RULE`로 제공한다.

release `saju-runtime-release-v1.4.0-63dc8d398e90`은 `calculate_saju_chart`만 승인하고 `calculate_saju_period`는 항상 차단한다. feature 기본 off와 production key 필수 조건을 유지하며 production provider 변경, 앱 연결, MIX20K-v3.1 생성·학습은 승인하지 않는다.

실행 정본과 수량·hash는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md)를 따른다.
