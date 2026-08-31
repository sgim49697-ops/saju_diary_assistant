<!-- default_profile_adr.md - 첫 한국 runtime profile의 후보 결정과 승인 보류 사유를 기록한다. -->

# ADR: `KR_CIVIL_MIDNIGHT_V1`

- 상태: candidate, production 기본값 승인 보류
- 결정일: 2026-08-31

첫 비교 profile은 한국 역사 civil time, 입춘 연 경계, 12절 월 경계, 00:00 일 경계, 민간시 시주, 진태양시 미적용, 지장간 본기 기준 지지 십신으로 고정한다.

이 선택은 제품의 최초 범위를 명확하게 만들고 서로 다른 학파 축이 한 결과에 섞이는 것을 막는다. 다른 일 경계나 진태양시가 필요하면 기존 profile을 수정하지 않고 새 policy ID를 만든다.

KASI 1900~2049 음양력 54,787일은 전수 mismatch 0으로 확보했다. 그러나 24절기 OpenAPI를 150년 전수 조회한 결과 2026-08-31 현재 2000~2028년 696건만 반환돼 12절 공식 날짜 Gate는 348/1,800에 머문다. 계산 provider로 빈 연도를 채우지 않는다.

1964년 KASI 역서 원문의 백로 `9월 7일 24시 00분`을 civil datetime `9월 8일 00:00`으로 정규화하면 Astronomy Engine은 일치하고 Skyfield는 불일치한다. 반대로 2021~2027년 비공식 월력 표시 분 84건은 Skyfield가 84/84, Astronomy Engine이 68/84다. 따라서 conformance v5는 어느 provider도 선택하지 않고 현재 runtime 구현도 변경하지 않았다. 원문이 분 정밀도이므로 sub-minute 물리 정확도를 판정한 것으로 확대하지 않는다.

`runtime_approved=false`와 candidate 상태를 유지한다. 엔진 자체 순간으로 만든 5,400건 전·경계·후 검사는 배정 로직의 내부 일관성일 뿐 순간 정확도의 근거가 아니다. 공식 coverage와 단일 적격 provider를 확보하고, 기술 conformance와 별도로 명리 정책 검토가 끝난 뒤에만 이 문서를 accepted ADR로 갱신한다.
