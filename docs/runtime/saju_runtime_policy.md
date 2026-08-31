<!-- saju_runtime_policy.md - 앱·평가 코드가 따라야 할 만세력 runtime 사용 규칙을 요약한다. -->

# 사주 Runtime 사용 정책

현재 계산 core의 v1 호환 경로와 `saju-runtime-python-v1.2.0` 후보가 구현됐지만 production 승인 상태는 아니다. 기존 v1·v1.1 계약과 보고서는 불변 보존한다.

- 기본 `SajuRuntimeEngine()`은 `RUNTIME_GATE_PENDING`을 반환한다.
- 로컬 개발 검증만 `enable_candidate_runtime=True`를 명시한다.
- 지원 범위는 대한민국·`Asia/Seoul`·1900~2049다.
- 오전/오후는 range로 유지하고 대표시각을 만들지 않는다.
- 생시 미상은 시주를 `null`로 두고 후보의 공통 사실만 사용한다.
- DST gap을 이동하거나 fold를 임의 선택하지 않는다.
- 모델에는 `status`, `hard_facts`, `fact_authority`, `code`, `message`, `limitations`만 보낸다.
- `HARD_CANDIDATE`를 학습 Gold나 전문가 인증 사실로 표현하지 않는다.
- 신강약·격국·용신·대운·자동 해석은 runtime fact payload에 넣지 않는다.
- 새 출생 파생 ID는 32바이트 0600 key 파일의 domain-separated HMAC-SHA256과 v2 prefix를 사용한다. production key가 없으면 fail-closed하며, 고정 test signer는 conformance에만 쓴다.
- KASI 분 표기는 KST 최근접 분·30초 half-up으로 비교한다. 이는 84건 실측 기반 프로젝트 규칙이지 KASI 공식 반올림 규정이 아니다.
- 엔진 간 한국 날짜 차이는 KASI 공식 행이 없으면 미해결로 차단한다. Skyfield나 Astronomy Engine이 서로를 판정하지 않는다.
- 120초 독립 엔진 차이 기준은 비권위 회귀 가드다.
- 활성 intake 계약은 `intake_registry-v1.1.0.json`이 고정한 session v2.1·FSM v1.1·Gate v1.1이다. 중복 `period` key가 있는 과거 session v2는 이력 보존용이며 앱에서 읽지 않는다.
- 구조화 intake FSM은 자유문을 파싱하지 않는다. 앱 계층이 검증된 event를 넘기며 release·production key·FSM 100/100·저장 암호화·보존 정책이 모두 준비돼야 계산 action을 낸다.
- chart·period tool 결과는 FSM이 발급한 현재 `scr2_` HMAC call ID를 그대로 되돌려야 한다. 이전 요청 결과, 렌더링 완료 결과, 현재 slot과 다른 `sif2_` fingerprint가 있는 cache는 거부한다.
- 활성 v1.2 계약·release·보고서와 CLI 입력은 모든 중첩 수준의 중복 JSON key를 거부한다. release 승인기는 보고서의 `gate_checks` boolean을 신뢰하지 않고 집계 metric에서 다시 계산한다.
- 미래 MIX20K-v3.1 생성·preflight는 v1.2 release ID·HMAC ID 계약·`approved_saju_runtime_v1_2` fact source를 전수 검증하며, release/key 전에는 source dataset을 읽지 않는다.

정본과 Gate는 [`saju_runtime_calculator_adoption.md`](../../implementation/plans/saju_runtime_calculator_adoption.md)를 따른다.
