<!-- 03_SAJU_MODEL_EVALUATION_AND_DATA_PLAN.md - 모델·학습 데이터 상태를 요약하고 원인 분리 후 조건부 보정으로 연결한다. -->

# 03. 모델 평가·데이터·학습 요약

## 문서 역할

이 문서는 모델·데이터의 **요약 문서**다. 실행 순서는 [로드맵 README](implementation/plans/saju_product_roadmap/README.md), 현재 상태는 [00 기준선](implementation/plans/saju_product_roadmap/00-current-baseline.md), 진단 상세는 [50 자동 모델 평가](implementation/plans/saju_product_roadmap/50-automatic-model-evaluation.md)가 소유한다. 여기서 별도 평가 수량·임계값·학습 순서를 만들지 않는다.

50의 전체 경로·통제 변수·파일 구현 순서는 [컨텍스트 진단 실행 설계](implementation/plans/saju_system_context_diagnosis.md)를 따른다. 모델 크기뿐 아니라 계산·대화 상태·정보 선택·지시문·이력·학습·검사·실제 배포를 함께 확인한다.

## 완료와 미완료

| 항목 | 상태 | 해석 |
|---|---|---|
| K0·KI10·KI20 | 고정 K0, KI10·KI20 Full FT 완료 | 학습 실패가 아니라 실제 응답의 원인 분리가 필요 |
| Phase 6 | 단회 완료, `AUTOMATED_REPAIR_REQUIRED` | 기존 판정 불변, 소비된 sealed blind 재열람·재사용 금지 |
| MIX2K-v4 LoRA | R8·R16·R32 각각 2,000행·250 step 완료 | 정식 5-arm 생성 비교 완료와는 별개 |
| 현재 R16 입력 | `v1.0.1/build-54836f556b4f` | [LoRA 계획](implementation/plans/mix2k_v4_chart_day_lora.md)의 고정 학습 이력 |
| 별도 v1.1 보정 | 마지막 checkpoint accepted 238/400 | 현재 R16에 미반영, 자동 재개·재학습하지 않음 |
| 20문장 기준선 | 3모델·60요청·54생성·6차단 완료 | 과거 개발 진단 이력 보존 |
| 최신 전체 경로 진단 | S0/S1·S2·CPU 재집계 보존, Phase 8A·8B S3·9 S4 검증 완료 | S3/S4 후보 미채택; Phase 10 R16 앱 후보 CPU 검증 완료, 다음 Phase 11, 추가 학습·운영 전환 미실행 |
| Phase 9 모델 비교 | S4 v1.1 192요청·172생성·20차단·오류 0 검증 완료 | 첫 실패 1건 별도 보존; 일부 개선·일반 대화 회귀·반복 출력 분리 |
| MIX20K-v3.0.1 | 보정·비학습 후보 이력 | 현재 2K 학습 데이터와 별도, v3.1 생성 승인 아님 |

## 최근 진단이 말해 주는 것

[S2 완료 기록](implementation/history/2026-09-15-system-context-diagnosis.md)의 최대 입력 1,692 token·삭제 이력 0·원응답은 보존했다. 후속 [CPU 재집계](implementation/history/2026-09-15-system-context-rescore.md)에서 R16 검사 오탐 6건은 2건 PASS·4건 판단 불가로 바뀌고 실제 MIN 오류 2건은 FAIL을 유지했다. [v1.16 앱 후보](implementation/history/2026-09-15-dashboard-v116-intent.md)는 당시 CPU canary를 통과했고 남은 오차단은 v1.17에서 추가 수정·검증했다. 앱 오차단 수정과 모델 품질은 별개다.

[Phase 8 완료 기록](implementation/history/2026-09-16-phase8-intent-s3.md#phase8b)의 S3는 96요청·86생성·10차단이다. P1에서 일부 형식·필수 사실 지표가 좋아졌지만 실제 날짜 누락·사실 혼동과 검사 한계가 남아 미채택했다. 이후 [Phase 9 결과](implementation/history/2026-09-16-phase9-s4-recovery.md#phase9)는 공통 P0에서 K0/3B×FULL/MIN 192요청을 완료했다. 3B가 일부 정정 사실·두 문장 형식에서 개선됐지만 불필요한 사주 삽입과 장문 반복도 확인됐고, 틀린 전제 교정 PASS는 네 조건 모두 0이었다. 크기만으로 해결됐다고 결론 내리지 않고 Phase 10의 직접 사실 응답·필요 정보/이력 분리 구현으로 연결했다. [v1.18 CPU 결과](implementation/history/2026-09-16-phase10-product-candidate.md#phase10)는 실제 R16 품질 개선의 측정 결과가 아니며 Phase 11 가설과 Phase 12 통합 확인을 구분한다. 원점수·scorer·운영 모델은 보존한다.

[20문장 완료 기록](implementation/history/2026-09-05-dashboard-prompt20.md)에서 연결 구조 검사 통과는 K0 8/13·R16 10/13·KI20 8/13이었다. R16의 일간·일주/일진 구분 개선이 있지만 세 모델 모두 틀린 일간 전제를 수용했다. 시간 범위, 개념 설명, 일반 대화 전환, 요청한 형식에서도 오류가 관찰됐다.

입력 token identity·부모 이력은 검증됐고 최대 입력 2,024 token·제외 대화 0이었다. 무조건 context 확대나 재학습을 시작할 근거는 없지만, 입력이 잘리지 않았다는 사실은 컨텍스트 간섭이 없다는 증거가 아니다. 필요한 정보만 준 조건과 전체 정보 조건을 작은/큰 기본 모델에 교차 적용한다. 유한 검사기의 오탐·누락 때문에 위 통과 수를 모델 정확도나 해석 품질로 부르지 않는다.

현재 R16 데이터에서 일반 공감 250행은 모두 원국 미연결이고 2,000행 중 1,751행이 3줄 이상 답변이었다. 이는 연결 중 일반 대화 전환과 짧은 형식 요청의 커버리지를 점검할 단서이지 원인 확정이 아니다. 특정 문자열 0건만으로 의미상 예제 부재를 단정하지 않는다. 세부 집계 조건은 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다.

## 다음 판단의 원칙

- 모델 오류·검사기 오류·미측정 품질을 분리하고 계산→상태→최종 입력→화면 경로부터 검증한다. 정보량 비교와 R16 현재 지시문 대 개선 후보 하나의 비교를 별도로 수행한다.
- 큰 동일 계열 Instruct 기본 모델 비교는 필수다. K0 1.3B 기본 모델을 기준으로 크기 가설을 점검하며 R16과 큰 기본 모델만의 차이를 크기 효과로 부르지 않는다.
- 모델별 공식 tokenizer/template·revision·정밀도·VRAM 조건을 실행 전에 등록한다. 다른 모델의 token ID 동일성을 요구하거나 메모리 부족 때 다른 계열·양자화로 자동 대체하지 않는다.
- 데이터·학습·serving 계약과 무결성을 처음부터 확인하고 비교 후 남은 오류와 연결한다. 별도 400건 보정의 범위가 이번 오류를 해결하는지도 이때 판단한다.
- S3의 P1은 prompt 파일과 formatter가 붙이는 지시를 합친 최종 시스템 지시문 묶음이다. 입력 JSON·역할·동결 부모 이력·필수 사실·출력 한도는 유지한다. 제품 응답 계약 변경을 S3에 섞지 않는다.
- [Phase 9](implementation/plans/saju_product_roadmap/phases/phase-09.md)의 첫 비교 후보는 `kakaocorp/kanana-2-3b-instruct`다. 공식 revision/hash·실행기·CPU 검증은 [구현 기록](implementation/history/2026-09-16-phase9-s4.md)에 등록했고 가중치 수집·GPU 비교는 [v1.1 완료 기록](implementation/history/2026-09-16-phase9-s4-recovery.md#phase9)에 분리해 보존한다. K0와 공통 P0, 각각 FULL/MIN을 비교하며 pruning·distillation·attention 구조 차이 때문에 순수 파라미터 수만의 인과 효과로 단정하지 않는다.
- [Phase 11](implementation/plans/saju_product_roadmap/phases/phase-11.md)에서 실제 teacher fallback 이력과 행동 7축을 확인하고 학습 가설·보정 대상·조건부 명세만 작성한다. 기존 400행은 accepted 238·초안 미판정 3·미작성 159 상태로, 단순 2,000+400 덧붙이기를 전제하지 않는다. [Phase 12](implementation/plans/saju_product_roadmap/phases/phase-12.md)의 실제 후보 결과를 대조한 뒤에만 학습 여부를 별도 결정하고 앱·지시문으로 해소됐으면 건너뛴다. 확인 묶음은 이후 학습이나 학습 후 새 평가에 재사용하지 않는다.
- [60 데이터 build](implementation/plans/saju_product_roadmap/60-mix20k-v3-1-build.md)와 [70 학습·승격](implementation/plans/saju_product_roadmap/70-training-and-promotion.md)은 조건부 후속이다. 진단 완료만으로 자동 진행하지 않으며 모델 크기·학습 방식·규모는 별도 결정이다.

## 평가·데이터 보존 원칙

측정 가능한 자동 계약만 사용하고 자연스러움·의미 품질은 계약이 없으면 `not_measured`로 남긴다. 계약 밖 평가를 완료 조건으로 추가하지 않는다. 진단 완료는 모든 응답의 정답이나 품질 승인을 뜻하지 않는다.

실제 모델 입력과 generation 조건을 고정하고, 새 검사/데이터/학습에는 새 version·build를 사용한다. 이미 노출된 20문장은 개발 진단이며 새로운 봉인 성능으로 주장하지 않는다. 과거 자동 보고서를 유리하게 덮어쓰거나 원출력·제한 데이터·checkpoint를 Git에 넣지 않는다.

[기존 Phase 정본](implementation/plans/saju_1b_10k_20k_baseline/README.md), [v3 후보 보정 정본](implementation/plans/mix20k_v3_repair_plan.md), LoRA의 versioned 계약은 당시 실행 범위를 보존한다. 과거 768 길이·최소 3문장/3줄·Full FT 지시를 새 실험의 자동 기본값으로 복사하지 않는다.

## 진행 기록

### 2026-09-16 — S4 구현과 실제 비교 상태 구분

- 모델 비교 실행기·공식 pin·CPU 회귀를 구현했으며 [검증 기록](implementation/history/2026-09-16-phase9-s4.md)에 연결했다. 잔여 예산 242와 실제 비교 미실행을 유지하고 모델·데이터 원인을 아직 확정하지 않는다.

### 2026-09-16 — 앱 후보·S3 완료와 다음 비교 연결

- 8A CPU/합성 화면과 8B 실제 모델 비교의 증거를 분리했다. P1 미채택·검사 한계·잔여 예산은 [실행 기록](implementation/history/2026-09-16-phase8-intent-s3.md)을 따르며 모델 크기나 데이터 단일 원인으로 단정하지 않는다. 데이터 보정·학습·운영 전환은 수행하지 않았다.

### 2026-09-15 — 학습 실행 시점 보완

- Phase 11 조건부 명세와 Phase 12 실제 확인 뒤 실행 결정을 구분했다. 문서 외 실행 상태는 그대로이며 [보완 기록](implementation/history/2026-09-15-phase7-canonicalization.md#supplement-20260915)을 따른다.

### 2026-09-15 — 모델·데이터 순서를 Phase 8~14에 정합화

- 지시문 묶음 통제·공통 P0의 3B 비교·행동 7축·400건 결정·조건부 단일 학습·실제 앱 확인을 새 Phase에 연결했다. 기존 결과와 680요청 총상한은 보존하며 검증은 [정본화 기록](implementation/history/2026-09-15-phase7-canonicalization.md)을 따른다.

### 2026-09-14 — 모델 단독 진단에서 전체 경로 비교로 구체화

- 입력 용량과 컨텍스트 간섭을 분리하고 정보 선택·크기 교차 비교를 50과 [새 실행 설계](implementation/plans/saju_system_context_diagnosis.md)에 연결했다. 검증 결과는 새 계획의 진행 기록을 따르며 다운로드·모델 실행·학습·서비스·기존 판정은 변경하지 않았다.

### 2026-09-05 — 보정·학습보다 원인 분리를 선행

- 완료된 3-rank 학습과 최신 20문장 진단을 반영했다. 비자동 평가 절차·필수 표본 작업·승격 조건과 중복 실행 지시를 제거하고 50~70 정본에 연결했다.
- 검증 명령·결과는 [재정렬 기록](implementation/history/2026-09-05-model-cause-roadmap.md)을 따른다. 모델 등록·다운로드·GPU 생성·400건 재개·추가 학습·기존 Gate 변경은 미실행이다.
