<!-- 40-day-relation-runtime.md - 원국과 단일 날짜 간의 결정론적 관계 존재만 계산하는 release를 정의한다. -->

# 40. 단일 날짜 Relation v1

## 포함

- 기간 연·월·일 천간의 일간 기준 십신.
- 기간 연·월·일 지지 본기의 일간 기준 십신.
- 원국 각 지지와 기간 일지 사이 합·충·형·파·해 존재.
- 동일 천간·지지 반복.
- `PROFILE_DETERMINISTIC`, table version, parent chart/period snapshot hash.

## 제외

- 관계 우선순위와 합화 성립 단정.
- 길흉 점수, 신강약, 격국, 용신.
- 이별·사고·합격 등 사건 변환.
- 2~31일 relation 배열.

## Gate

- 천간 십신 10×10과 지지 본기 10×12 전수 일치.
- 합·충·해·파 pair 대칭성, 형 group·자형의 명시적 방향 규칙 검증.
- 존재하지 않는 관계 생성 0, interpretation field 0.
- 단일 날짜가 아니거나 relation release가 없으면 fail-closed.

dashboard v1.13은 v1.12를 부모로 단일 날짜에만 relation card와 model allowlist를 추가한다. 주·월 범위는 날짜 label만 유지한다.
