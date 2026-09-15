<!-- 60-mix20k-v3-1-build.md - 원인 분리 후 데이터 변경이 필요한 경우에만 새 비학습 build를 설계한다. -->

# 60. 조건부 데이터 build와 비학습 preflight

## 상태: 조건부 보류

실행 순서는 [로드맵 README](README.md), 원인 분리는 [50번 문서](50-automatic-model-evaluation.md)를 따른다. **50 완료만으로 이 단계는 자동 진행하지 않는다.** 데이터 변경 필요성·최소 범위·새 계약·실행 승인이 모두 있어야 한다. 이번 문서 정합화는 MIX20K-v3.1 생성이나 보정 400건 재개를 승인하지 않는다.

## 원인별 최소 변경

현재 후속 대응은 [Phase 11/S5 가설](phases/phase-11.md) → [Phase 12 실제 결과 대조](phases/phase-12.md#training-decision) → [조건부 Phase 13](phases/phase-13.md)이다. 11에서는 보정 대상·조건부 명세만 작성하고 실행은 확정하지 않는다. 12에서 정상 입력의 잔여 모델 오류와 데이터 변경 필요성을 확인한 뒤에만 별도 실행을 결정한다. 앱·지시문으로 해소됐으면 기존 400건·명세가 있어도 보정·학습을 건너뛴다. 새 자료로 이미 소비한 Phase 12 확인 질문을 재사용하지 않는다. 400건은 자동 추가가 아니며 실제 builder의 교체/보정/결합 계약과 최종 행 수를 고정한다. teacher fallback의 실제 provider/분리 판정·검사 문법 편향 가능성·7개 행동 축·self-reported used_fact_paths의 한계를 함께 점검한다.

- 지시문만으로 해소된 오류에는 데이터 확대를 요구하지 않는다.
- facts/slot 전달이 잘못된 경우에는 입력·상태 구현의 별도 수정과 회귀를 먼저 제안한다.
- 올바른 입력에서도 남고 데이터 분포와 연결되는 실패만 표적 보정 후보로 둔다. 연결 상태의 일반 대화·전제 교정·시간 범위·출력 형식 커버리지를 50-D 결과에 따라 정한다.
- 기존 400건 보정과 새 표적 범위가 같다고 가정하지 않는다. 기존 accepted 행·불변 build를 수정하거나 현재 R16에 이미 적용됐다고 기록하지 않는다.

## MIX20K-v3.1 검토를 다시 열 경우

기존 v3.0.1의 chart 4,350회·period 900회, canonical 대기 3,800행·중복 참여 2,035행은 [보정 정본](../mix20k_v3_repair_plan.md)의 과거 후보 통계다. 그 수량대로 즉시 생성하라는 지시가 아니다. v1.2 전용 generator와 승인된 원본·config는 그대로 보존하고 새 builder/version에서 다음을 계약화한다.

- 승인 chart·일별 기간·단일 날짜 관계의 정확한 scope/version으로 재계산하고 provisional·unsupported fact를 차단한다. 연간·해외·범위 관계 등 미지원 사례는 실패/불확실성 궤적으로 분리한다.
- model argument와 executor 권한을 분리하고 chart ID·timezone·policy·revision·키를 모델/공개 출력에 노출하지 않는다. production key를 데이터 생성에 쓰지 않으며 build 전용 key는 Git 제외 0600으로 관리한다.
- 지시문·tokenizer·template·형식·행 및 assistant-token 분포·중복·허용 길이를 새 계약으로 동결한다. 과거 `768`이나 최소 3문장/3줄을 새 데이터의 기본값으로 복사하지 않는다.
- canonical mismatch·state/grounding·truncation·zero mask·EOS·serialization·parser 오류, split 누수를 자동 검사한다. consumed sealed payload를 열지 않고 승인된 fingerprint 경계만 사용한다.
- 새 build ID와 manifest·공개 집계를 만들고 `training_promotion_allowed=false`로 종료한다. 원문·제한 데이터·내부 ID는 공개 보고서에 넣지 않는다.

[70 학습·승격](70-training-and-promotion.md)은 별도 결정이다. 2K 보정이 10K·20K 확대를 자동 승인하지 않는다.

## 진행 기록

### 2026-09-15 — 실제 후보 확인 뒤 조건부 생성 결정

- Phase 11 명세만으로 보정·학습에 진입하지 않도록 Phase 12 결과 대조를 연결했다. 기존 원본과 권한은 보존하며 [보완 기록](../../history/2026-09-15-phase7-canonicalization.md#supplement-20260915)을 따른다.

### 2026-09-15 — 조건부 Phase 11·13 연결

- [반입 원문](source-20260915.md)의 데이터 원칙을 조건부 실행에 연결했다. 원본 build·400건 state·teacher·생성·학습은 변경하지 않았다. 검증은 [정본화 기록](../../history/2026-09-15-phase7-canonicalization.md)을 따른다.

### 2026-09-05 — 생성 우선 지시를 조건부 후속으로 전환

- 기존 v3.1 재생성 수량·768 상한을 현재 실행 지시에서 과거 후보 참조로 분리했다. 50-D 결과에 따라 표적 범위를 먼저 고정하며 기존 build·계약·승인 상태는 변경하지 않는다.
- 검증 명령·결과는 [재정렬 기록](../../history/2026-09-05-model-cause-roadmap.md)을 따른다. 실제 builder 구현·데이터 생성은 미실행이다.
