<!-- default_profile_adr.md - 첫 한국 runtime profile의 후보 결정과 승인 보류 사유를 기록한다. -->

# ADR: `KR_CIVIL_MIDNIGHT_V1`

- 상태: candidate, production 기본값 승인 보류
- 결정일: 2026-08-31

첫 비교 profile은 한국 역사 civil time, 입춘 연 경계, 12절 월 경계, 00:00 일 경계, 민간시 시주, 진태양시 미적용, 지장간 본기 기준 지지 십신으로 고정한다.

이 선택은 제품의 최초 범위를 명확하게 만들고 서로 다른 학파 축이 한 결과에 섞이는 것을 막는다. 다른 일 경계나 진태양시가 필요하면 기존 profile을 수정하지 않고 새 policy ID를 만든다.

현재 KASI 1900~2049 전수 음양력과 12절 경계 fixture가 없으므로 `runtime_approved=false`를 유지한다. 기술 conformance와 별도로 명리 정책 검토가 끝난 뒤에만 이 문서를 accepted ADR로 갱신한다.
