<!-- saju_external_conformance.md - 공개 사주 계산 fixture의 신뢰도·정책 경계·사용 범위를 기록한다. -->

# 사주 계산 외부 conformance 기준

## 결론

공개 웹 테스트는 학습 데이터의 임의 보강이나 단일 정답 엔진 선정에 사용하지 않는다. 공식 KASI 음양력 값은 `primary_snapshot`, 고정 revision 오픈소스 테스트는 `comparative`, 현재 생성기에 쓰는 `lunar-python`은 `consistency_only`로 분리한다.

이 suite는 다음 세 용도로만 사용한다.

1. 향후 앱 runtime 계산기의 날짜·정책 conformance
2. 구조화 명식 deterministic QA의 독립 검산
3. 명식이 없는 LLM 입력에서 계산기 handoff를 요구하는 통합 테스트

기존 10K/20K, source blind test, 사람 해석 Gold에는 섞지 않는다. Skyfield 기반 v1.3 candidate runtime은 구현·검증됐지만 release·production engine은 승인하지 않았으며 `configs/saju_calculation_policy.json`의 `runtime_enabled=false`를 유지한다.

## 근거 계층

| 계층 | 고정 원천 | 사용 범위 |
|---|---|---|
| primary snapshot | [KASI 음양력 정보 OpenAPI](https://www.data.go.kr/data/15012679/openapi.do) | 양력↔한국 음력, 윤달, 달력 연·월 간지, 일진 |
| comparative | [`oh-my-saju@a8c0cf6`](https://github.com/JaeSang1998/oh-my-saju/tree/a8c0cf64adfde8fb1ce253112529edd8e90cc94b) | KASI fixture provenance, 60갑자·절입·자시·태양시 property 참고 |
| comparative | [`manseryeok@fba3253`](https://github.com/yhj1024/manseryeok/tree/fba3253d7305b8b61189bd78318a7a27ed8c9b09) | KST 4주 표본, 절입·자시·진태양시·십신·공망 정책 사례 |
| comparative | [`korean_saju@8c81256`](https://github.com/glee1228/korean_saju/tree/8c81256db10d3179d31f551beacaa69f2ab2fcc5) | 경계 반례를 찾는 제3 비교 구현 |
| consistency only | [`lunar-python@448f397`](https://github.com/6tail/lunar-python/tree/448f397c1695cadab3899bf460e0042cab7f0e66) | 현재 데이터 생성기와 KASI snapshot의 차이 진단 |
| comparative | [`ssaju@07b608a`](https://github.com/golbin/ssaju/tree/07b608a778be6dac8669e04b9ab794c441959208) | 기존 정책 리뷰의 회귀 비교 |

KASI API는 양력·음력·윤달·`lunWolgeon`·`lunSecha`·`lunIljin`·율리우스 적일을 제공하지만 완성된 사주 4주, 절입시각, 진태양시나 자시 학파 정책을 제공하는 API는 아니다. 특히 KASI의 달력 연·월 간지를 사주 연주·월주와 같은 필드로 alias하지 않는다.

## 고정 fixture

- `kasi_lunar_200.json`: 1583년 이후 양력→한국 음력 200행, SHA-256 `d651d5a7…8ca1a`
- `policy_cases_20.jsonl`: KST 4주 11건, 자시 3건, 입춘 2건, 진태양시·천간 십신·지장간 정기·공망 각 1건
- KASI fixture 수집기 revision: `jinill1/korean-lunar-calendar@6f988e3f50a424d165b9834f9e28cd3ea962da63`

`lunar-python==1.4.8`과 200행을 advisory 비교하면 완전 일치 136행, 하나 이상 차이 64행이다. 음력 날짜 3행과 달력 월간지 64행이 다르고, 윤달·달력 연간지·일진은 0건이다. 이 결과는 KASI snapshot을 수정할 근거가 아니며 `lunar-python`을 독립 oracle로 승격하지 않는다.

## 정책 분리

`hard_fact` 후보는 KASI snapshot의 날짜 변환·윤달·달력 간지·일진처럼 공식 필드 의미가 명확한 값이다. `policy_bound`는 정책 profile을 함께 기록한 4주, 절입, 자시, 진태양시, 지장간 정기 십신, 공망이다. 서로 다른 profile의 결과는 정답/오답으로 합치지 않는다.

다음 값은 외부 구현에 테스트가 있어도 Gold에서 제외한다.

- 신강약 점수
- 격국·용신
- 대운 시작 나이처럼 학파별 산식이 필요한 값
- 신살 해석과 관계 우선순위
- 자동 해석문, 색상·방향·소품 등 보완 조언

## 라이선스

KASI 공개 API 페이지는 무료·이용허락범위 제한 없음으로 표시된다. 복제한 200행 fixture는 수집기 저작권자 Jinil Lee의 MIT 조건과 provenance를 보존한다. `manseryeok` 테스트에서 구조화한 정책 사례에는 원 revision과 MIT 저작권 고지를 유지한다. 자세한 내용과 전문은 [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) 및 `licenses/third_party/`에 있다.

## 현재 Gate

- fixture schema·원천 revision·SHA-256·라이선스 고지: 구현 완료
- KASI 달력 간지와 사주 연주·월주 semantic alias 차단: 구현 완료
- 학습·blind 혼입 차단: 구현 완료
- KASI 음양력 1900~2049년 54,787/54,787일: 양음력·일진 mismatch 0
- KASI 24절기 OpenAPI 1900~2049년: 150/150년 scan 완료, 실제 반환 2000~2028년 696건
- KASI 공식 현재 계산 1920~2100년: 4,343/4,344행, 유일한 누락은 비절입인 2030년 우수, 절입 2,172/2,172행
- `KR_CIVIL_MIDNIGHT_V1` v1.3 candidate runtime: Skyfield 1.55·고정 DE440s·내장 UT1 결합 완료
- runtime↔독립 validator: 1,800개 TT root·UTC·표시 분 mismatch 0, 전·정확·후 5,400개 경계 mismatch 0
- 권한 분리: `PROFILE_DETERMINISTIC` 240, `PAST_OFFICIAL_CORROBORATED` 1,280, `FORECAST_DIAGNOSTIC_NONAPPROVAL` 280
- strict runtime provider: 공식 원시 분 라벨 mismatch 22/1,560과 미래 물리 순간 미판정으로 실패
- 앱 runtime 계산 엔진 승인: `runtime_approved=false`, release registry 없음, feature flag 기본 off
- MIX20K-v3.1 재생성·학습: 보류
- 전문가 해석 Gold 승인: 보류

실행 정본과 최신 공개 보고서는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md) 및 [`data/reports/saju_runtime_conformance/v1.6.0/build-8bd88d6db03a/`](../../data/reports/saju_runtime_conformance/v1.6.0/build-8bd88d6db03a/)에서 확인한다. 후보 runtime은 기본 off이며, 승인 전 결과는 `HARD_CANDIDATE`를 넘지 않는다.
