<!-- saju_runtime_policy.md - 앱·평가 코드가 따라야 할 만세력 runtime 사용 규칙을 요약한다. -->

# 사주 Runtime 사용 정책

현재 `saju-runtime-python-v1.0.0`은 구현된 후보이며 production 승인 상태가 아니다.

- 기본 `SajuRuntimeEngine()`은 `RUNTIME_GATE_PENDING`을 반환한다.
- 로컬 개발 검증만 `enable_candidate_runtime=True`를 명시한다.
- 지원 범위는 대한민국·`Asia/Seoul`·1900~2049다.
- 오전/오후는 range로 유지하고 대표시각을 만들지 않는다.
- 생시 미상은 시주를 `null`로 두고 후보의 공통 사실만 사용한다.
- DST gap을 이동하거나 fold를 임의 선택하지 않는다.
- 모델에는 `status`, `hard_facts`, `fact_authority`, `code`, `message`, `limitations`만 보낸다.
- `HARD_CANDIDATE`를 학습 Gold나 전문가 인증 사실로 표현하지 않는다.
- 신강약·격국·용신·대운·자동 해석은 runtime fact payload에 넣지 않는다.

정본과 Gate는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md)를 따른다.
