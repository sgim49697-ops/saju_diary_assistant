<!-- saju_system_context_diagnosis.md - 계산·상태·컨텍스트·모델·학습·화면의 원인을 분리하는 실행 설계 정본이다. -->

# 사주 대화 전체 흐름·컨텍스트 진단 계획

| 항목 | 값 |
|---|---|
| 계획 버전 | `saju-system-context-diagnosis-v1.2.1` |
| 이전 계획 버전 | `saju-system-context-diagnosis-v1.2.0`, `saju-system-context-diagnosis-v1.1.0`; 문서·CPU 재집계 완료 이력 보존 |
| 보존된 부모 실행 계약 | `saju-system-context-diagnosis-v1.0.0` |
| 작성일 | 2026-09-14 |
| 문서 부모 | `f34f8562f24116558cd39fbc2a69cea430bb1158` |
| 응답 기준선 | `26462137f9a4ef34adb2d3db0dd6eaff6282b309`의 20문장 진단 |
| 현재 단계 | S0/S1·S2·CPU 재집계 및 S3 96요청 실행·재구성 검증 완료 |
| 최신 S2 build | `build-c39b4bce5089`, 312생성·30사전 차단; 검사기 오탐 별도 확인 |
| 현재 승인 후속 | Phase 8A v1.17 CPU/화면 `build-49b9aed70565`·8B S3 `build-ffd985905b51` 완료 |
| 다음 별도 작업 | Phase 9 S4 실행기·공식 pin·CPU 검증 완료; 수집·실제 GPU 비교 범위 확인 |

2026-09-14 PR #28로 이 계획과 부모 v1.15 후보를 `master`에 통합했다. 2026-09-15 사용자 승인 범위인 S0/S1 구현·검증과 S2 실행을 완료했다. 코드 통합과 격리 진단은 운영 v1.15 배포가 아니다. [완료 기록](../history/2026-09-15-system-context-diagnosis.md)은 실제 모델 오류와 검사기 오탐을 구분한다. 입력·집계 재구성 검증 통과가 검사기의 의미 타당성이나 모델 품질 승인은 아니다.

## 1. 먼저 쉽게 정리

문제는 “작은 모델인가, 데이터인가” 둘 중 하나만 고르는 일이 아니다. 계산 결과가 맞아도 앱이 불필요한 사주 정보를 계속 붙이면 현재 질문을 놓칠 수 있고, 같은 입력에서도 모델에 따라 영향을 다르게 받을 수 있다. 학습 예제가 그런 답변 습관을 강화했을 가능성도 별도로 확인한다.

**입력이 잘리지 않았다는 사실은 컨텍스트 간섭이 없다는 증거가 아니다.** 용량은 글을 얼마나 넣을 수 있는지, 정보 선택은 지금 질문에 무엇이 필요한지, 활용 능력은 그중 무엇을 따라 답하는지의 문제다. 여기서 컨텍스트 간섭은 관련 없는 배경정보·이전 답변 때문에 현재 요청의 정확도나 형식 준수가 나빠지는 관찰을 뜻한다. 출력 비교만으로 attention 내부 원리를 확정하지 않는다.

다음 순서로 확인한다.

1. 실제 실행 중인 코드·모델과 시험한 코드·모델이 같은지 확인한다.
2. 계산 → 대화 상태 → 모델 입력 → 원응답 → 화면 사이에서 정보가 바뀌는지 확인한다.
3. **같은 작은 모델에 전체 정보와 질문에 필요한 정보만 각각 제공**한다.
4. 정보는 고정하고 지시문만 바꾼다. 이어서 **작은 기본 모델과 큰 기본 모델 모두에 전체/최소 정보를 제공**한다.
5. 남은 오류를 학습 데이터·학습 설정과 대조하고, 확인된 원인에 맞는 최소 수정만 제안한다.
6. 새 합성 질문에서 수정 후보를 확인한다. 추가 학습이나 운영 반영은 별도 결정이다.

## 2. 정본 관계와 이번 변경 범위

- 2026-09-15 [검토 3문서 전량 반영](saju_product_roadmap/source-20260915.md)으로 사용자 선택의 Phase 7~14를 후속 실행 단위로 사용한다. 기존 Phase 0~6·S0~S6·50-A~D의 의미와 승인 Gate는 유지한다. **이 진단 자체가 별도 Phase나 release Gate를 만들지 않는다.** Phase 8B=S3, 9=S4, 11=S5, 12=S6이며 앱 8A·제품 모드 10은 동결 실험과 분리한다.

- [후속 로드맵](saju_product_roadmap/README.md)은 실행 순서, [00 기준선](saju_product_roadmap/00-current-baseline.md)은 현재 상태, [50 진단](saju_product_roadmap/50-automatic-model-evaluation.md)은 A~D 단계와 종료 경계를 소유한다.
- **이 문서는 50이 위임한 전체 흐름의 실험 설계·변수·실행 파일 순서 정본**이다. 별도 Phase나 release Gate를 만들지 않는다. 50-B는 컨텍스트 비교와 지시문 비교로 세분하고, 50-C는 크기×컨텍스트 교차 비교로 구체화한다.
- [학습 Phase 정본](saju_1b_10k_20k_baseline/README.md), [Runtime 정본](saju_runtime_calculator_adoption.md)의 승인 범위와 과거 불변 산출물은 그대로다. 50의 단계를 바꾸려면 이 문서와 50·로드맵 연결을 함께 갱신한다.
- 2026-09-14 계획 작성은 문서·테스트 정합화였고 이후 PR #28로 통합했다. 2026-09-15 승인 범위는 S0/S1과 S2까지다. P0 교체·큰 모델 다운로드·teacher 호출·데이터 생성·400건 재개·학습·서비스 전환·브랜치 병합은 실행하지 않는다.
- 2026-09-15 후속 승인으로 새 진단 검사기와 기존 S2 응답의 CPU 재집계, 별도 앱 v1.16 의도 정책 후보·합성 canary를 진행한다. 이전 S2·v1.15·grounding v2의 파일과 결과는 변경하지 않는다. 운영 앱 교체·추가 GPU 생성·S3 이후 실험은 승인 범위 밖이다.
- 2026-09-16 별도 구현 승인으로 Phase 8A 오차단 후보·CPU/합성 화면 검증과 Phase 8B/S3 96요청을 완료했다. 아래 2026-09-15 승인 범위는 당시 이력이며, 이번에도 Phase 9 이후·큰 모델 다운로드·데이터 보정·학습·운영 전환은 하지 않았다. [실제 실행 기록](../history/2026-09-16-phase8-intent-s3.md)을 따른다.
- 2026-09-16 후속 구현 요청으로 [S4 실행기·공식 3B 등록·CPU 검증](../history/2026-09-16-phase9-s4.md)을 추가했다. 가중치 다운로드·최대 192요청의 실제 비교는 범위 확인 전까지 보류하며, 실제 모델 비교 과제는 미실행으로 유지한다. 학습·데이터·서비스·기존 scorer/보고서·새 24문항은 변경하지 않는다.

## 3. 확인된 사실과 아직 모르는 것

| 확인 근거 | 확인된 범위 | 아직 결론 내릴 수 없는 것 |
|---|---|---|
| [20문장 공개 집계](../../data/reports/saju_1b_baseline/dashboard-prompt20/v1.0.0/build-9ab2958c83dc/aggregate.json)·[검증](../../data/reports/saju_1b_baseline/dashboard-prompt20/v1.0.0/build-9ab2958c83dc/verification.json) | 60요청·54생성·6사전 차단; K0 8/13·R16 10/13·KI20 8/13은 연결 구조 검사 통과 수 | 모델 정확도·설명 품질·일반 성능 순위 |
| [20문장 완료 기록](../history/2026-09-05-dashboard-prompt20.md) | 최대 입력 2,024 token·제외 대화 0; 첫 질문 12개 그룹 token identity 일치; 잘못된 일간 전제를 세 모델이 수용 | 컨텍스트 간섭 부재, 모든 사례의 전달 정상, 작은 모델 한계 확정 |
| [현재 원국 지시문](../../configs/chat_prompts/saju_bound_chart_v2.txt) | 사주 설명 역할·보통 세 문장 이상 권고·일부 미지원 기간 질문에도 원국 설명을 먼저 요구 | 일반 대화·두 문장 요청 실패의 원인 확정; 지시문 단일 변수 비교 필요 |
| [v1.15 입력 조립·생성](../../scripts/training/phase5_dashboard_v1_15.py) | 연결 시 bound profile, system에 runtime 정보 결합, 엔진별 대화 이력 사용; 길이 초과 때 오래된 user/assistant 쌍 제외 | 현재 운영 v1.14가 후보와 동일하다는 보장; 긴 대화·정정·동시 세션의 무결성 |
| [LoRA 실행 이력](mix2k_v4_chart_day_lora.md) | R8/R16/R32 모두 2,000행·250 step 완료; `max_length=2048`; full snapshot 학습; 전체 rendered 최대 1,960 token, truncation·loss 누출 0 | 8K라는 작업명만으로 장문 학습 완료, 낮은 training loss만으로 더 좋은 대화 |
| 같은 LoRA 이력·[분포 점검](../history/2026-09-05-model-cause-roadmap.md) | 승인 fallback 초안 2,000건; 최종 Claude 판정 191·Codex 별도 판정 1,809; 일반 공감 250행 모두 원국 미연결, 답변 1,751행이 3줄 이상 | 교차 provider 조건 충족, 특정 문자열 0건만으로 의미상 예제 부재, 현재 오류의 단일 원인 |
| [00 기준선](saju_product_roadmap/00-current-baseline.md) | 최신 코드·계획은 master 통합, 운영은 v1.14 유지; 400건 보정은 현재 R16에 미반영 | 코드 병합만으로 후보가 이미 운영에 적용됐다는 가정 |
| [S3 완료 기록](../history/2026-09-16-phase8-intent-s3.md#phase8b) | R16/P0·P1 96요청·86생성·10차단, 일부 형식 개선과 실제 오류·검사 오탐/누락 병존 | P1 일괄 개선, 모델 크기·데이터 단일 원인, 현재 KI20 대비 개선 |

위 수치의 생성 시점은 각 원본 기록을 따른다. 2026-09-14에는 문서·코드·공개 집계와 Git·서비스 상태를 대조했으며 새 응답을 생성하거나 private 학습 자료를 다시 전수 판정하지 않았다. 계산기 승인은 날짜·원국·관계의 제한된 계산 사실에 대한 것이지 성격·길흉 해석의 정답을 보장하는 승인이 아니다.

## 4. 전체 경로와 원인 가설

`합성 출생 입력 → 승인 계산 facts → 대화 상태·연결 → 현재 의도·정보 선택 → 지시문·이력·token → 모델/adapter → 원응답 → 자동 검사·화면`

학습 데이터·학습 설정은 모델의 응답 습관에, 배포된 코드·설정은 위 경로 전체에 영향을 준다. 아래 가설은 동시에 성립할 수 있다.

| 가설 | 구분할 문제 | 확인·반증 비교 | 수정 대상 후보 |
|---|---|---|---|
| H-RUNTIME | 사실 자체·날짜 권한·불확실성이 틀림 | 합성 입력의 승인 계산값과 요청 시각·label·range/unknown을 독립된 고정 기대값에 대조 | 계산·요청 계약; 모델이 계산하도록 우회하지 않음 |
| H-STATE | 수정한 생일, 원국, 날짜, 엔진의 이전 상태가 남음 | 정정 전후 revision·부모 hash·새 세션 연결·재시작·동시 요청 CPU 재생 | FSM·binding·저장·캐시 |
| H-ROUTE | 현재 요청보다 연결 여부가 응답 모드를 결정 | 원국 연결을 유지한 일반 대화와 동일한 비연결 일반 대화; 선택한 fact 경로 추적 | 의도 분류·정보 선택 정책 |
| H-CONTEXT | 불필요한 정보량·위치·반복이 질문을 압도 | C_FULL/C_MIN, 길이 대조 C_PAD, 같은 사실의 위치 변경; 필요한 사실 변경의 양성 대조 | 모델용 projection·배치·중복 제거 |
| H-PROMPT | 지시문과 현재 요청이 충돌 | 고정 R16·동일 facts/이력에서 P0/P1 하나만 변경 | 지시문·응답 계약 |
| H-HISTORY | 앞선 잘못된 답변이나 오래된 상태에 매임 | 정답·오답 부모 이력을 미리 고정해 동일 후속 질문 재생; 스스로 생성한 이력은 별도 분석 | 이력 선택·정정 상태 전달 |
| H-SIZE | 작은 모델이 정보 선택·지시 준수를 충분히 못함 | K0/큰 동일 계열 기본 모델 × C_FULL/C_MIN의 2×2 | 모델 크기 후보; 입력 개선과 병행 가능 |
| H-TRAIN | full snapshot·정형 장문·태스크 분포·학습법의 영향 | 같은 크기 K0/R16/KI20 비교 후 데이터 분포·실제 loss mask·설정 연결 | 데이터 계약·LoRA/Full FT 조건 |
| H-SERVE | tokenizer·adapter·정밀도·길이·종료·캐시가 달라짐 | 최종 token과 실행 모델 hash·실제 generation 인자·cache 초기화·종료 사유 대조 | 추론 경로 |
| H-SCORE | 맞는 답을 실패로, 틀린 개념을 통과로 셈 | 합성 정답/오답/인용/부정/모순 fixture와 원응답·화면 답변 대조 | 검사기·표시·저장 |
| H-DEPLOY | 시험한 개선이 실제 앱에 없음 | 실행 PID의 cwd/commit/config/model과 진단 manifest 비교 | 배포 절차; 이번 계획으로 교체하지 않음 |

### S1에서 고정할 추적 계약

각 합성 요청에 다음을 하나의 연결된 trace로 기록한다. 앱에서 모델까지 정확히 전달됐는지 확인하기 전에는 해당 실패를 모델 크기로 귀속하지 않는다.

- 입력: case ID·고정 서버 KST 시각·의도·필수 fact 경로·기대 상태와 기대 사전 차단 사유.
- 계산/상태: 승인 release·schema·source hash, uncertainty, 상태 revision·부모 binding·정정 후 무효화 여부. 운영 HMAC·opaque ID를 공개 trace에 복사하지 않는다.
- 모델 입력: 선택/제외한 fact 경로와 이유, system/runtime/history/current-user별 token 수·위치, 최종 messages/token identity, 제외된 이력과 이유. 합성 원문·token 배열도 private trace로 분리한다.
- 추론: 코드·config·모델/adapter hash, 실제 선택 엔진, tokenizer backend/template, 정밀도, decoding·입출력/전체 길이 제한, seed, cache/retry 여부, stop reason, 지연·최대 VRAM.
- 출력: 원응답·자동 판정·최종 표시/저장 응답의 개별 hash와 변경 사유. 생성 0건인 사전 차단은 모델 실패나 생성 성공으로 세지 않는다. 자동 보정·재시도로 실패를 덮어쓰지 않는다.

합성 입력만 사용한다. 실제 출생정보는 작은 탐색 공간이므로 단순 hash로 공개해도 안전하다고 간주하지 않는다. 키·인증정보·private 경로·실제 출생 원문·원시 응답은 Git과 공개 보고서에서 제외한다.

## 5. 비교 조건 — 변수를 섞지 않는다

### 5.1 정보 조건

| 조건 | 구성 | 해석의 제한 |
|---|---|---|
| C_FULL | 현재 승인된 모델 공개 allowlist의 전체 snapshot | 내부 원본 전체나 비공개 필드를 추가하는 조건이 아님 |
| C_MIN | 질문에 필요한 동일 사실·관계·날짜·불확실성과 필수 권한 정보를 보존한 최소 projection | 실행 전 task schema로 필수 경로 고정; 응답을 보고 정답 힌트를 추가하지 않음 |
| C_NONE | 일반 대화의 계산 사실 생략 또는 필수 근거가 없는 음성 대조 | 근거가 필요한 사주 질문에서 C_FULL과 동등 과제로 채점하지 않음 |
| C_PAD | C_MIN에 안전하지만 무관한 합성 자료를 넣어 C_FULL token 길이의 ±1%에 맞춤 | padding이 완전히 중립적인 의미를 갖는다고 가정하지 않음; 길이와 의미 효과가 남음 |
| C_POS_FRONT/MIDDLE/END | 같은 C_PAD의 동일 사실 묶음을 DATA 영역 앞/중간/끝으로 이동 | role·권한 우선순위와 마지막 사용자 질문은 유지; 위치 외 정보량은 동일 |

일반 대화의 C_MIN은 원국을 연결 해제하지 않고 모델용 사주 사실만 생략할 수 있다. 저장된 원국·승인 여부·보안 정책은 그대로다. 계산 사실을 묻는 질문에서는 C_MIN이 모든 필수 경로를 보유하는지 CPU 검사로 보장한다. 한 필수 사실을 바꾸면 답도 바뀌어야 하는 양성 대조와, 무관한 사실만 바꾸면 요구 결과가 유지돼야 하는 불변 대조를 함께 둔다. 무조건 모든 컨텍스트를 무시하는 모델이 좋은 점수를 받지 않게 한다.

보존된 필드의 이름·값·단위·역할·직렬화 규칙은 C_FULL과 같게 유지한다. 자연어 요약이나 새로운 정답 설명을 C_MIN에 섞지 않는다. 현재 승인 schema의 필수 필드를 삭제해야 하는 설계라면 해당 schema를 억지로 통과시키지 말고 별도 모델용 projection 계약을 먼저 검증한다. C_FULL/C_MIN은 정보 선택의 묶음 효과이며, 길이만의 효과라는 주장은 C_PAD와 위치 대조의 한계를 함께 확인한 뒤에만 검토한다.

P0는 현재 지시문, P1은 **개선 후보 하나**다. P1은 승인 fact 우선·잘못된 전제 교정·range/unknown 유지·일반 대화 전환·요청한 문장 수 우선만 명확히 한다. P0/P1 비교는 동일 R16·C_FULL·동결 부모 이력에서 **시스템 지시문만 바꾼다**. C_MIN과 P1을 동시에 적용한 결과를 어느 한쪽의 단독 효과로 부르지 않는다.

P1은 파일만이 아니라 `_runtime_model_context_from_binding()`이 덧붙이는 원국·일진 안내까지 포함한 최종 지시문 묶음 하나로 정의한다. 두 위치의 충돌을 점검하고 변경 구간·원문·hash·token 차이를 고정한다. JSON 사실·직렬화·역할·부모 이력·생성 조건·기존 case 요구 fact는 유지한다. 질문별 정답을 추가하지 않는다. 단순 일진에 원국 사실까지 요구하는 기존 계약과의 충돌은 한계로 남기고 사후 완화하지 않는다. 제품 계약 변경은 [Phase 10](saju_product_roadmap/phases/phase-10.md#response-contract)에서 동일 기준으로 양쪽 후보를 확인한다. S3는 최대 96요청으로 종료하고 P2/P3를 반복하느라 S4를 미루지 않는다.

### 5.2 모델·추론·이력 통제

- 작은 모델 비교는 K0·R16·KI20의 동일 K0 tokenizer/backend/template·동일 사실·동일 동결 부모 이력·동일 decoding을 사용한다. 지시문 비교는 R16 한 모델로 제한한다.
- 크기 비교는 **K0 1.3B 기본 모델 ↔ 큰 동일 계열 Instruct 기본 모델**이다. 두 모델에 P0를 공통 적용하고 각각 C_FULL/C_MIN을 실행한다. 지시문 승자를 모델별로 골라 넣지 않는다. R16과 큰 기본 모델만 비교해서 크기 효과를 주장하지 않는다.
- 큰 후보는 저장소·정확한 revision·공식 라이선스·공식 tokenizer/template·가중치 hash·지원 길이·정밀도·VRAM/KV cache·offload 계획을 먼저 등록한다. `kakaocorp/kanana-2-3b-instruct`의 정확한 revision·공식 hash를 [S4 전용 등록](../../configs/model_versions/saju_1b_baseline/kanana-2-3b-s4-v1.0.0.json)에 고정했고 가중치는 아직 다운로드하지 않았다. 같은 정밀도가 불가능하면 미실행 사유를 적고 별도 결정을 받는다. 다른 계열·양자화로 자동 대체하지 않는다. 한쪽만 offload하거나 검증 환경 전체를 최신화하지 않는다. 공식 카드의 가지치기·증류 계보, 실제 구성·loader·라이선스는 [Phase 9](saju_product_roadmap/phases/phase-09.md)에서 확인하며 모델명·공개 benchmark로 자원·사주 품질을 단정하지 않는다.
- **서로 다른 모델의 token ID 동일성은 요구하지 않는다.** 의미상 동일한 메시지를 각 공식 template로 렌더링하고 token 길이 차이를 기록한다. 현재 dashboard loader는 K0 tokenizer와 고정 token 설정을 사용하므로 큰 모델을 기존 엔진 이름만 바꿔 실행하지 않는다. 별도 versioned adapter에서 tokenizer·BOS/EOS/PAD·지원 구조를 검증한다.
- 입력 4,096·출력 4,096은 현재 후보의 안전 상한이지 모든 후보의 지원 보장이 아니다. 두 모델의 공식 전체 길이 범위 안에서 공통 상한을 실행 전에 고정한다. train `max_length=2048`, serving 입력 상한, 출력 상한, 입력+출력 총량을 따로 기록한다.
- 주 비교는 `do_sample=false`, 동일 generation 규칙과 무삭제 동결 부모 이력으로 수행한다. 이력이 제외되거나 필수 facts가 빠진 행은 비교 부적격으로 분리하고 새 버전에서 해결한다. 이미 완료한 report에 일부 행을 갈아 끼우지 않는다.
- 첫 질문, 고정 부모 후속 질문, 각 모델 자신의 답을 따라가는 end-to-end 대화는 별개다. 본 원인 비교의 예산은 고정 부모 재생만 포함한다. 자체 이력 연속 생성은 필요성이 확인된 후 별도 예산·승인을 받아 수행하고 섞어 평균내지 않는다.
- cache는 arm·세션 사이에 초기화하고 adapter 전환·배치 순서·동시 세션 CPU 테스트를 둔다. 공유 GPU lock·유휴 상태·실제 여유 VRAM·native JIT 조건을 확인하기 전 GPU를 사용하지 않는다. 현 서비스에 실험 설정을 덮어쓰지 않는다.

## 6. 단계·질문 묶음·실행 예산

### 단계 순서와 완료 산출물

| 단계 | 50 대응 | 수행 내용 | 완료 산출물 / 다음 조건 |
|---|---|---|---|
| S0 | 50-A | Git·세션·실행 코드·config·모델·학습 부모와 공개 보고서 연결, 기존 테스트 실패 분류 | 관측 시점이 있는 manifest 초안; 운영/후보 불일치와 환경/코드 결함 구분 |
| S1 | 50-A | 전체 trace·합성 사례·필수 facts·자동 검사 회귀·privacy·dry-run 구현; 기존 데이터/학습 계약 목록화 | CPU 테스트와 입력 재구성 통과; 모델 오류를 판단할 비교 자격 확보 |
| S2 | 50-B1 | 작은 3모델 C_FULL/C_MIN; R16 길이/위치 대조 | 정보 축별 개선·회귀·불변·부적격 수; 실패도 보존 |
| S3 | 50-B2 | 고정 R16/C_FULL의 P0/P1 비교 | 단일 지시문 효과·회귀; P1 채택 강제 없음 |
| S4 | 50-C | 작은/큰 기본 모델 × C_FULL/C_MIN | 크기 효과와 컨텍스트 민감도의 차이; 미실행이면 사유·대안 분리 |
| S5 | 50-D | 결과를 데이터·loss mask·학습/serving 차이와 대조 | 원인별 근거·반증·최소 변경 제안; 400건 유지/재설계/보류 판단 |
| S6 | 50-D | 후보 하나를 새 합성 질문에 확인; 실제 경로 CPU·격리 앱 회귀 | 최종 비교 집계·한계·다음 작업; 배포/학습 승인이 아님 |

질문은 이미 노출된 [20문장](../../SAJU_CHAT_TEST_PROMPTS.md)을 개발 회귀로 보존하되 이를 새 성능 자료로 재포장하지 않는다. 새 원인 비교는 **8개 층 × 6개 = 48개 평가 대상 turn**, 확인용은 **8개 층 × 3개 = 24개 평가 대상 turn**이다. 각각 필요한 부모 대화는 합성 fixture로 미리 고정하며 부모 생성을 추가 GPU 요청으로 숨기지 않는다.

8개 층은 ① 사실·개념 구분 ② 틀린 전제 교정 ③ 원국/일진·날짜 범위 ④ 범위/미상/음력 모호성 ⑤ 정보 정정·재연결 ⑥ 연결 상태 일반 대화·공감 ⑦ 문장 수·재작성 ⑧ 후속 질문·오답 이력이다. 지원/미지원 요청, C_NONE 음성 대조, 필수 사실 변경/무관 정보 변경 대조를 층 안에 배정하고 실행 전에 case별 기대값·분모를 고정한다.

48개와 24개는 부모 대화·합성 출생 seed·질문 변형 계열 단위로 분리한다. 알려진 개발 질문·허용된 학습 build와 중복을 자동 검사한다. 24개 확인용 묶음은 P1·projection·검사기를 동결한 뒤 한 번 사용하고 그 결과로 동일 묶음에 맞춰 다시 조정하지 않는다. 소비된 sealed blind와 무관한 새 합성 진단이며, 이후 학습 자료로 재사용하지 않는다.

S1에서 후보 선택 규칙도 고정한다. 계산 권한·상태·privacy에 회귀가 있는 후보는 제외하고, 사전 지정한 현재 요청 준수 항목의 paired 결과와 자원 비용으로 후보 하나를 고른다. 지표 간 우열이 엇갈리면 임의 가중 평균으로 승자를 만들지 않고 미채택으로 남긴다. 채택 후보가 없으면 S6은 개선 주장 없이 기준선 재현·미채택 사유를 기록하고 추가 후보 탐색을 별도 후속으로 둔다.

| 비교 | 최대 요청 계산 | 최대 요청 |
|---|---|---:|
| S2 작은 모델·정보 | 48 × 2조건 × 3모델 | 288 |
| S2 위치·길이 보조 | 미리 정한 사실 질문 12 × 4조건 × R16 | 48 |
| S3 지시문 | 48 × 2지시문 × R16 | 96 |
| S4 크기·정보 | 48 × 2조건 × 2기본 모델 | 192 |
| S6 새 합성 확인 | 24 × 2경로: R16/P0/C_FULL과 선택 후보 하나 | 48 |
| 실행 적격성 소규모 확인 | tokenizer·cache·메모리·종료 확인 | 8 |
| 합계 상한 | 위 요청 합계 | 680 |

680은 **요청 상한이지 GPU 생성 완료 수가 아니다**. 동일 immutable 실행 identity의 결과만 재사용할 수 있고 재사용 수를 별도로 표시한다. 요청 = 신규 생성 완료 + 검증된 재사용 + 예상 사전 차단 + 예상 밖 차단/오류 + 미실행으로 전량 대조한다. 결과가 나쁘다고 조건·seed·재시도를 추가하지 않는다. 예상 벽시계 시간은 소규모 확인의 실제 속도로 산출한다. **680 전체 예산은 승인하지 않았다.** 2026-09-15 승인 상한은 S2와 적격성 확인을 합친 344였고 342요청(288+48+6)을 등록·완료했다. 2026-09-16 별도 승인 S3 96요청을 완료해 누적은 438이며 추가 적격성 요청은 0이다. GPU 유휴·여유 12GiB 이상에서 순차 진행하고, 경쟁 작업은 종료하지 않는다.

## 7. 자동 판정과 원인 해석

[Phase 12의 공통 평가](saju_product_roadmap/phases/phase-12.md#evaluation-common)는 계산·상태, 모델 원응답, 앱 요청 수행을 분리하며 S3/S4부터 적용한다. 앱 직접 조회·확인 응답을 모델 성공으로 세지 않고 일반 오차단·불필요 확인·미지원 보호·원응답/저장/API/화면·cold/warm 비용을 별도 집계한다. 현재 원국 연결과 현재 응답 모드는 Phase 10에서 분리하고 S3/S4의 동결 경로에 반영하지 않는다.

- 각 검사 항목은 `PASS / FAIL / UNSCORABLE`로 구분한다. 정답 값의 단순 포함만 검사하지 않고 역할·부정·인용·모순 최소 fixture를 포함한다. 규칙이 해석하지 못한 문장은 `UNSCORABLE`이며 통과로 계산하지 않는다. 자동 계약 밖 자연스러움·의미 품질은 `not_measured`이고 새 완료/승격 blocker가 아니다.
- 사실 모순·틀린 전제 교정·필수 fact 사용·range/unknown 보존·정정 상태·불필요한 사주 삽입·문장 수/재작성·날짜 사전 차단·max-token hit·개인정보 비노출·지연/VRAM을 따로 집계한다. 각 항목의 적용 가능 수·자동 판정 가능 수·실제 생성 수를 함께 공개한다.
- paired 비교는 같은 case/부모의 점수 차이를 사용한다. 공통으로 판정 가능한 쌍의 수와 조건별 `UNSCORABLE` 비율을 함께 표시해 어려운 답변을 제외해 생기는 착시를 드러낸다. 전체 합계와 층별 결과를 모두 보고하며 서로 다른 의미의 지표를 한 정확도로 합치지 않는다.
- 재표본 구간을 낼 경우 부모 대화 그룹을 단위로 하고 seed `20260914`, 10,000회, 95% 구간을 실행 전에 고정한다. 동일 부모 turn을 독립 표본으로 부풀리지 않는다. 작은 묶음·넓은 구간·상이한 하위 결과는 불확실성으로 기록하며 개선 확정으로 포장하지 않는다.
- 각 기본 모델의 `C_MIN − C_FULL` 차이를 구한 뒤 두 모델의 그 차이를 대조한다. 큰 모델이 전체 정보에서만 크게 좋아졌는지, 최소 정보에서도 좋아졌는지를 나눈다. **크기×컨텍스트 상호작용**은 “모델 또는 컨텍스트 중 하나”로 강제 환원하지 않는다. 같은 계열이어도 사전학습 데이터·학습량·후처리는 다를 수 있어 순수 파라미터 수 인과 효과는 아니다.
- 한계와 다음 조치를 기록하는 것이 진단의 종료 조건이다. 실행하지 못한 필수 큰 모델 비교는 `not_executed`와 구체적 필요 조건을 남기고 전체 진단 완료로 세지 않는다. 부정적인 비교 결과는 정상적인 진단 결과이며 품질 승인 실패와 혼동하지 않는다.

| 결과 패턴 | 다음 조치 |
|---|---|
| 계산/상태/전달 단계에서 이미 값이 틀림 | 해당 앱·계산 경로부터 수정 제안; 모델 재학습으로 덮지 않음 |
| 원응답은 맞지만 검사·화면 결과가 다름 | 검사기·후처리·표시 경로 수정 제안 |
| 같은 모델에서 C_MIN이 개선되고 필수 사실 사용도 유지 | 질문별 projection/선택 정책 우선 후보; 모든 정보를 삭제하는 처방은 아님 |
| P1만으로 개선되고 다른 축 회귀가 없음 | 지시문 후보의 새 질문 확인; 자동 데이터 확대 없음 |
| 큰 기본 모델이 두 정보 조건에서 일관되게 개선 | 크기 변경의 정확도·지연·VRAM 절충 제시; 자동 승격 없음 |
| 작은 모델이 전체 정보에서만 크게 나빠짐 | 정보 선택과 크기 모두 후보; 상호작용 근거 기록 |
| K0는 유지되지만 R16/KI20에서 특정 전환·형식이 악화 | 데이터·학습 설정 비교; Full FT/LoRA 자체의 단일 원인으로 단정하지 않음 |
| 최소 정보·큰 모델에서도 동일 오류 | 기대값·지시문·검사 계약·태스크 정의 재점검 |

## 8. 데이터·학습·계산기·앱에서 빠뜨리지 않을 점

S0/S1에서는 목록과 무결성을 먼저 확인하고 S5에서 응답 결과와 연결한다. 데이터 점검을 큰 모델 결과가 나올 때까지 전부 미루지 않는다.

- 데이터: 현재 R16의 실제 2K build와 별도 400건·20K 후보를 분리한다. 출처·허용 범위·teacher fallback·split/부모 대화 중복·과도한 유사 답변·태스크 분포·연결 여부·정정/후속 대화·짧은 형식 요청을 집계한다. 문자열 0건은 의미 커버리지 부재의 증명이 아니다.
- 학습: 실제 base/adapter hash·학습한 행·assistant-only mask·EOS/PAD·packing 경계·truncation·중단/재개·최종 reload를 확인한다. 입력이 길고 답이 짧은 분포와 최소 3문장/3줄 계약의 영향은 가설로 남긴다. rank별 loss만으로 승자를 고르지 않는다.
- 학습/serving 차이: full snapshot과 projection, bound/intake 지시문, relation 제공 여부, 메시지 역할, tokenizer/template, 길이, assistant prefill과 종료 규칙을 같은 표로 대조한다. 새 relation 정보를 현재 R16이 학습한 것처럼 취급하지 않는다.
- 계산기: 승인된 과거 chart-only·정오 날짜 label·기간/관계 범위만 기준으로 사용한다. exact/range/unknown, 절입 불확실성·50분 격리, 서버 기준일·KST·기간 상한·부모 변조·미승인 미래 물리 시각 차단은 모델 비교와 무관하게 보존한다.
- 앱: 원국을 계산만 한 상태와 대화에 연결한 상태, 날짜 변경·생일 정정 후 새 연결, 새 세션/재시작/동시 요청의 분리를 확인한다. 인증·권한·rate limit·암호화·로그 비노출은 유지한다. 모의 CPU canary와 실제 GPU 생성·실제 서비스 배포는 별도 상태로 기록한다.
- 확인 단계: S6의 합성 24개는 격리된 후보 앱의 실제 요청→표시 경로를 지나게 한다. CPU 상태·HTTP 테스트와 필요한 브라우저 흐름도 확인하되 UI 동작 성공을 응답 품질 성공으로 바꾸지 않는다. 운영 service 교체는 별도 승인·rollback 계획 뒤에만 검토한다.

## 9. 파일 점검·구현 순서

아래 연결은 **현재 존재하는 점검 대상**이다. 파일 순서는 S0→S1의 조사 순서이며 모두를 수정하라는 지시가 아니다.

| 순서 | 현재 파일 | 확인 내용 |
|---:|---|---|
| 1 | [기준선](saju_product_roadmap/00-current-baseline.md), [20문장 runner](../../scripts/evaluation/dashboard_prompt20.py) | 실행 부모·manifest·합성 질문·이력 재생 |
| 2 | [원국 engine](../../scripts/runtime/calculation/engine_v1_4.py), [일진 engine](../../scripts/runtime/calculation/engine_v1_5.py), [기간](../../scripts/runtime/period_v1/engine.py), [관계](../../scripts/runtime/relation_v1/engine.py) | 기대 facts·날짜/불확실성·승인 범위 |
| 3 | [intake FSM](../../scripts/runtime/intake_fsm_v1_2.py), [앱 adapter](../../scripts/runtime/chart_day_adapter.py), [원국 binding](../../scripts/runtime/chart_day_dashboard_binding.py) | 정정·복원·부모 상태·동시 세션 |
| 4 | [모델 projection](../../scripts/runtime/chart_day_model_projection.py), [v1.15 대시보드](../../scripts/training/phase5_dashboard_v1_15.py), [bound 지시문](../../configs/chat_prompts/saju_bound_chart_v2.txt), [intake 지시문](../../configs/chat_prompts/saju_intake_runtime_v2.txt) | 의도·정보 선택·메시지 조립·이력 |
| 5 | [tokenizer](../../scripts/training/dashboard_tokenizer_v1.py), [grounding 검사](../../scripts/training/dashboard_grounding_v2.py), [후보 replay](../../scripts/evaluation/dashboard_v115_replay.py) | 최종 token·모델 loader·원응답/표시·오탐/누락 |
| 6 | [데이터 계약](../../scripts/data/mix2k_v4_contracts.py), [teacher](../../scripts/data/mix2k_v4_teachers.py), [finalizer](../../scripts/data/mix2k_v4_finalize.py), [LoRA](../../scripts/training/mix2k_v4_lora.py) | 입력 출처·분포·학습과 serving 차이 |

S0/S1의 **구현된 실행 파일**은 아래 순서다. 과거 미구현 제안 파일 표기는 이번 구현으로 대체한다.

1. `configs/model_versions/saju_1b_baseline/system-context-diagnosis-v1.0.0.json`: 입력·arm·모델 등록·예산·privacy·판정 계약. 응답을 보기 전에 고정한다.
2. `scripts/evaluation/system_context_contracts.py`: schema·경로/중복/변조·필수 fact·실행 자격 검증. 기존 유틸리티를 재사용한다.
3. `scripts/evaluation/system_context_cases.py`: 새 합성 48개·고정 부모·승인 adapter 재생·음성/양성 대조. S6의 24개는 아직 만들거나 사용하지 않았다. 기존 20문장이나 불변 build를 수정하지 않는다.
4. `scripts/evaluation/system_context_diagnosis.py`: 추적·dry-run·순차 실행·안전 재개·public aggregate/manifest 검증. 별도 `system_context_projection.py`, `system_context_scoring.py`, `system_context_backend.py`가 정보 선택·자동 계약·격리 추론을 담당한다. 큰 모델 loader는 이번 범위가 아니며 기존 운영 코드를 변경하지 않는다.
5. `tests/test_system_context_diagnosis.py`: 단계 1~4의 CPU 계약·trace·정보 선택·검사기·개인정보·예산·재개 테스트를 기능과 함께 추가한다.

GPU 없이 계약과 dry-run을 먼저 닫는다. 실행 단계는 구현된 CLI의 도움말·실제 테스트에 맞춰 별도 진행 기록에 확정한다. 새 source/config/scorer가 생기면 version/build를 올리고 승인된 부모 파일을 덮어쓰지 않는다.

### 부모 S0~S2 실행 명령 — 당시 동결 코드 전용

다음 명령은 부모 실행의 재현 기록이다. 현재 `master`에는 새 source가 추가되어 있으므로 완료된 부모의 검증에는 아래 후속 절의 `system_context_rescore verify-parent`를 사용한다. 이 목록은 GPU 진단 재실행이나 부모 build에 새 응답 추가를 지시하지 않는다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.system_context_diagnosis validate-contract
.venv-data/bin/python -B -m scripts.evaluation.system_context_diagnosis plan
.venv/bin/python -B -m scripts.evaluation.system_context_diagnosis execute
SYSTEM_CONTEXT_DIAGNOSIS=S0_S1_S2_V1 .venv/bin/python -B -m scripts.evaluation.system_context_diagnosis execute --execute
.venv/bin/python -B -m scripts.evaluation.system_context_diagnosis verify --build <동결된-build-ID>
```

실행·재구성은 기존 `.venv` ML 환경을 사용한다. GPU 실행 전에 검증된 project-local Python 3.10 headers를 `CPATH`에 추가하고 native JIT를 유지한다. 중단 뒤에는 동일 명령에 `--resume --build <동결된-build-ID>`를 추가한다. 완료 행은 검증 후 재사용하고, 시작됐으나 결과가 없거나 오류인 행은 재생성하지 않는다. 적격성 6건 중 오류가 있으면 본 비교는 시작하지 않는다. 시작 행·raw trace·모델 출력은 `runs/SYSTEM-CONTEXT-DIAGNOSIS/`에 0700/0600으로 보존하며 Git에 추가하지 않는다.

## 10. 종료·보존·후속 결정

후속 실행의 전체 순서는 [Phase 7~14 로드맵](saju_product_roadmap/README.md)을 따른다. [예산 정본](saju_product_roadmap/phases/phase-07.md#budget)은 Phase 7 당시 잔여 338과 S3 완료 후 현재 잔여 **242 = 본 비교 240 + 적격성 2**를 구분한다. 이는 실행 승인이 아니다. 추가 실제 모델 호출·학습 후 평가는 목적·범위·예산을 별도로 정하며 CPU 모의 검증과 구분한다. Phase 11은 학습 가설·보정 대상·조건부 명세, Phase 12는 실제 제품 결과 대조 후 Phase 13 실행 여부 결정, Phase 14는 운영 검토다. 앱·지시문으로 해소됐으면 기존 400건이나 명세가 있어도 학습을 건너뛴다. 미시험 3B/P1·새 projection은 새 통합 후보이며 S6 첫 확인의 한계를 밝힌다. 새 학습 검증에 이미 사용한 S6를 재사용하지 않는다.

S6 후보 하나는 [Phase 12](saju_product_roadmap/phases/phase-12.md#confirmation)의 모델·지시문 묶음·정보 선택·라우팅·이력 정책·검사·채택 규칙을 포함한 전체 실행 구성이다. 새 24문항 사용 전에 동결하며 결과를 보고 같은 질문으로 재선발하지 않는다. 실험 R16/P0/C_FULL 기준선과 운영 KI20의 비교·미측정·되돌림은 [Phase 14](saju_product_roadmap/phases/phase-14.md#comparison-baselines)를 따른다. 이 차이를 이유로 S6 비교군을 늘리지 않는다.

- S0~S6마다 `planned / implemented / validated / executed / not_executed`를 구분한다. 문서 존재를 구현 완료나 실행 완료로 세지 않는다. 현재 S0/S1은 `validated`, S2는 `executed`·공개 build `verified`, S3는 `executed`·공개 build `verified`, S4는 실행기 `implemented`·CPU 검증 완료/실제 비교 `not_executed`, S5~S6은 `not_executed`다. S1의 기존 계약·CPU 검증 완료와 S2/S3에서 추가 발견한 검사 문법 결함은 구분한다.
- 공개 파일에는 합성 사례의 집계·계약·manifest·코드/버전 hash와 한계만 기록한다. 원시 trace·질문에 결합된 계산 내용·모델 출력·token 배열은 Git 제외 private 경로에 두고 최소 권한·보존/삭제 정책을 검증한다.
- 기존 Phase 6·grounded-dialogue 원시 결과와 소비된 sealed blind는 열거나 재사용하지 않는다. 자연스러움 등 계약 밖 품질은 `not_measured`로 남기고 계약 밖 평가를 완료 조건으로 추가하지 않는다.
- Phase 6·Runtime release·production 허용·기본 모델·feature 기본 off는 자동 변경하지 않는다. [60 데이터](saju_product_roadmap/60-mix20k-v3-1-build.md)·[70 학습](saju_product_roadmap/70-training-and-promotion.md)은 원인별 결과에 따른 별도 결정이다.
- 종료 보고는 “어디서 잘못됐는가 / 무엇으로 확인했는가 / 무엇은 아직 모르는가 / 다음에 고칠 최소 범위”를 답한다. 실패가 남아도 근거 없는 전면 재학습이나 모델 교체를 처방하지 않는다.

### S2 이후 승인 후속 범위 — 2026-09-15 재집계·앱 CPU 완료 이력

1. 검사기 새 버전에서 인접 label/value, 간접 부정, 정정 전/현재 범위, 이전 답변 인용을 합성 양성·음성 fixture로 보강한다. R16 자동 회귀 8개 중 6개가 이 문제였으므로 기존 13→6 PASS를 실제 정확도 하락으로 사용하지 않는다.
2. 같은 S2 private 합성 응답을 재생성 없이 읽어 새 검사 버전의 파생 집계를 만든다. 원래 build·config·scorer·공개 집계는 보존한다. 새 GPU 호출·학습·sealed blind 접근은 필요하지 않다. 새 계약 `system-context-rescore-v1.0.0.json`과 `system_context_rescore` CLI에서 부모 공개 3파일 pin·기존 source 460파일·입력/응답 684파일을 검증한다. 새 파일 추가로 부모의 전체 source fingerprint가 달라져도 기존 검증기를 완화하지 않고 고정 부모 map을 읽기 전용으로 검증한다.
3. 검사가 허용하는 범위를 정리한 뒤 S3 지시문 단일 변수 비교, S4 큰 기본 모델×정보 비교를 진행한다. 이번 세 모델은 모두 1.3B이므로 크기 원인은 미확정이다. 입력 축소나 모델 교체를 자동 채택하지 않는다.
4. 모델 전 사전 차단으로 확인된 일반 공감 문장의 날짜 의도 오탐은 별도 앱 v1.16 후보로 수정한다. P0·모델·생성 계약·앱 사실 검사 문법은 유지하고 새 `dashboard_grounding_v3`의 의도 정책만 바꾼다. 단일 날짜 추출·KST·snapshot 일치 규칙은 그대로이며 애매한 날짜 후속은 계속 보수적으로 처리한다. 부모 사전 차단 30건을 새 응답이나 새 허용 사례로 바꾸지 않는다.

### 완료된 CPU 후속의 실행 순서와 격리 계약 — 이력

1. 새 진단 검사기의 부정·인용·과거 범위·인접 역할·정정·판단 불가 합성 회귀를 먼저 고정한다. 실제 312응답의 새 점수를 보고 규칙을 조정하지 않는다.
2. 아래 CPU 명령으로 부모 검증 → dry-run → immutable 파생 발행 → 별도 재검증을 수행한다. 342요청 중 312응답을 재채점하고 사전 차단 30건을 유지한다. 적격성 6건은 비교 분모에서 제외하며 모든 적용 지표의 PASS/FAIL/UNSCORABLE 전이를 공개한다.
3. 앱 후보는 v1.15의 독립 version snapshot이다. 모듈 전역 상태를 교체하는 wrapper를 사용하지 않으며 HTTP·직접 생성·새 프로세스·감사 metadata가 같은 의도 정책을 사용한다. 기본 포트 8769·별도 private 세션 경로·feature 기본 off, UI 자산은 v1.15를 그대로 재사용한다.
4. 합성 입력·대체 생성기로 CPU canary를 실행한다. 현재 v1.14 서비스·포트 8767은 교체하지 않는다. 진단 검사기 개선과 앱 사실 검사기 변경을 혼합하지 않는다.
5. 결과 집계·한계·부모 보존을 검증하고 `master`에 한글 커밋·push한다. S3/S4는 다음 별도 승인 단계다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore validate-contract
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore plan
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore verify-parent
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore execute
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore execute --execute
.venv-data/bin/python -B -m scripts.evaluation.system_context_rescore verify --build <파생-build-ID>
```

새 명령은 모델 파일·tokenizer를 불러오지 않는다. trace는 `runs/SYSTEM-CONTEXT-RESCORE/v1.0.0/`의 0700/0600 파일에만 보존하고, 공개 경로에는 aggregate·build manifest·verification만 발행한다. 자연스러움·해석 의미는 계속 `not_measured`이며 추가 완료 조건으로 요구하지 않는다.

앱의 CPU 실행·재검증은 다음 명령을 사용한다. 모델을 로딩하는 CLI worker 전체나 운영 브라우저를 실행한 것으로 표시하지 않는다. [후보 완료 기록](../history/2026-09-15-dashboard-v116-intent.md)에 정책 한계·18사례·경로 격리·공개 근거를 고정한다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary execute --execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary verify --build build-641ac655f656
```

### Phase 8A·8B 완료 — 2026-09-16

8A는 v1.17/grounding v4·의도 v2, CPU 45개·합성 브라우저 6개를 검증했다. 직전 사용자 발화만 읽기 전용으로 참조하며 이력·snapshot·모델 입력은 재구성하지 않았다. S3에는 이 라우팅을 적용하지 않았다.

S3의 구현된 실행 파일은 `scripts.evaluation.system_context_s3`이며 기존 `system_context_diagnosis`를 S3 runner로 재사용하지 않는다. P1 지시문 묶음·config·source·모델·입력 hash를 동결한 `build-ffd985905b51`에서 96요청·86생성·10차단을 완료했다. 입력 통제·비용·PASS/FAIL/UNSCORABLE·검사 오탐/누락·실제 오류는 [8B 완료 기록](../history/2026-09-16-phase8-intent-s3.md#phase8b)을 따른다. P1은 미채택이며 S4에 공통 P0를 적용하는 계획을 유지한다.

완료 build의 읽기 전용 검증 명령은 다음과 같다. 실행·resume 명령은 위 완료 기록의 당시 환경을 따르며 완료된 요청을 다시 생성하지 않는다.

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s3 verify --build build-ffd985905b51
```

## 11. 실험 설계의 참고 근거

- 긴 입력에서 관련 정보의 위치에 따라 성능이 달라지는 관찰을 위치 대조의 근거로 삼는다. 이는 이 저장소 모델의 원인이 이미 증명됐다는 뜻이 아니다. [Liu 외, Lost in the Middle, v3](https://arxiv.org/abs/2307.03172v3).
- 무관한 자료가 포함된 산술 문제의 성능 저하 연구를 정보 관련성 대조의 근거로 삼는다. 해당 과제 결과를 한국어 사주 대화나 모델 크기의 인과 결론으로 직접 일반화하지 않는다. [Shi 외, Large Language Models Can Be Easily Distracted by Irrelevant Context, v3](https://arxiv.org/abs/2302.00093v3).

## 진행 기록

### 2026-09-16 — S4 구현·공식 모델 등록·CPU 검증

- `system_context_s4` 전용 실행기·models/backend/projection·새 scorer v1.2를 구현했다. 기존 S2/S3와 8A를 재검증했으며 192요청·172생성 상한·20사전 차단·추가 preflight 0을 고정했다. 파일별 역할·실제 명령 순서·검증 실패와 복구·남은 제한은 [기록](../history/2026-09-16-phase9-s4.md)에 모은다.
- GPU 생성 0, 가중치 수집·실제 tokenizer 렌더링/dry-run·비교는 미완료다. 누적 438·잔여 242와 운영·학습·release 권한을 유지한다.

### 2026-09-16 — 8A 후보·S3 96요청 실행 완료

- `c1f090f`·`2d951b4`를 master에서 검증·푸시하고 S3를 실행했다. 부모 source·입력/출력·P0·scorer는 보존했고 내장·별도 verify가 같은 공개 hash로 통과했다. 실제 실행·항목별 결과·비용·검사 한계는 [실행 기록](../history/2026-09-16-phase8-intent-s3.md)에 고정한다.
- 다음 과제는 Phase 9/S4이며 큰 모델 등록·다운로드·GPU 실행은 하지 않았다. 운영 v1.14 PID 3144071·재시작 0·기본 KI20·feature off와 기존 학습·Phase 6·계산 release를 유지했다.

### 2026-09-15 — Phase 7 보완에 따른 경계 정합화

- 예산 잔여 338과 실행 권한, 전체 후보 구성 동결, Phase 11 가설→12 실제 결과→13 실행 결정을 구분했다. 검증 결과는 [보완 기록](../history/2026-09-15-phase7-canonicalization.md#supplement-20260915)에 남기며 기존 실행 계약·S0~S2·GPU·학습·운영은 변경하지 않는다.

### 2026-09-15 — Phase 7 정본화·최종 지시문 통제 보강

- 원문 3개·841줄을 후속 Phase 문서와 행별 연결했다. 계획 v1.2.0은 최종 지시문 묶음·첫 3B 후보·제품/모델 평가 분리·예산을 구체화한 문서 버전이며 부모 실행 config v1.0.0과 응답·검사·승인은 바꾸지 않았다.
- [정본화 기록](../history/2026-09-15-phase7-canonicalization.md)에 검증 결과를 남긴다. S3~S6·새 GPU 생성·teacher·학습·운영 전환은 미실행이다.

### 2026-09-15 — 앱 v1.16 의도 오탐·CPU canary 검증 완료

- v1.15 본체·config의 독립 snapshot과 새 grounding v3를 추가했다. 일반 대화/주제 제외/별도 운세 요청을 구분하고 HTTP·직접 호출·새 프로세스·저장 진단 metadata에 같은 의도 정책을 적용했다. 부모의 날짜·사실 문법·P0·모델·생성 계약은 유지한다.
- CPU canary `build-641ac655f656`: 회귀 25개, 경로별 의도 행렬 18개(허용 7·차단 11), 새 프로세스의 모델 라이브러리 로딩 0. 별도 verify 통과, 보고서 안전 회귀 포함 29개 통과. 실제 모델 생성·운영 앱 전환은 하지 않았다.
- 실제 Claude Code의 의도 반례 3개를 재현해 회귀를 보강했다. 초기 canary의 rate limit 충돌은 테스트 서버 예산 초기화 순서로 수정하고 실제 제한은 보존했다. 상세 검증과 한계는 [완료 기록](../history/2026-09-15-dashboard-v116-intent.md)을 따른다.
- 최종 검증: 전체 ML 환경 unittest 936개 전부 통과·건너뜀 0(84.149초), 정본·링크·자동 정책 표적 21개·Ruff·diff 검사 통과. v1.16/파생 상태를 요구하도록 과거 상태 고정 문서 assertion 2개를 갱신하고 공개 집계의 분모·승인 불변 회귀를 추가했다. 두 공개 build는 별도 verify·동일 실행 재사용에서 같은 hash를 유지했다.
- 이번 승인 후속은 완료다. 운영 v1.14 PID `3144071`·재시작 0·기존 코드와 모델, feature off, Phase 6·Runtime release·학습·승격을 유지했다. S3~S6과 실제 모델 로딩 worker/운영 브라우저 검증은 실행하지 않았으며 다음 별도 과제는 S3 지시문 단일 변수 비교다.

### 2026-09-15 — 동결된 S2 응답 312개 CPU 재채점·재검증 완료

- 구현 `53c251a`로 파생 `build-8547c487c858`를 발행했다. 342요청·312재채점·30기존 차단을 대조했고 별도 verify·동일 실행 재사용이 같은 hash로 통과했다. 적격성 6건은 비교 분모에서 제외했다.
- [파생 완료 기록](../history/2026-09-15-system-context-rescore.md)에 전이·보조 12개·한계를 고정했다. 기존 R16 오탐 6건은 2 PASS·4 UNSCORABLE로 바뀌었고 실제 MIN 오류 2건은 FAIL을 유지했다. 필수 사실 FULL/MIN은 R16 11/24·6/24 PASS, 판단 불가 7·8개다. 모델 성능 향상으로 읽지 않는다.
- 독립 지표 변경 0, 원본 460 source·684 입력/응답·공개 부모 3파일 보존. 새 GPU 생성·학습·운영 교체·S3/S4는 실행하지 않았다.

### 2026-09-15 — 승인 후속의 새 검사기와 읽기 전용 부모 검증

- 새 `role-aware-contract-v1.1.0`은 기대 정답을 참조하지 않는 주장 추출과 범위·부정·정정 trace를 제공한다. 지원 문법 밖의 label/value와 불확실 표현은 판단 불가로 남긴다. 문장 수·날짜·비공개 필드·토큰 종료 등 독립 지표는 그대로다.
- 실제 Claude Code를 읽기 전용으로 호출했다. 제시한 5개 반례를 재현하여 부정 삽입어·인용 앞의 오류 단서·불확실 표현·역할 교차 정정을 수정했다. 가정절을 현재 사실로 승격하는 제안은 채택하지 않고 판단 불가 회귀로 고정했다.
- 진단 검사기 16건·재집계 11건의 CPU 회귀가 통과했다. 합성 부모의 byte/hash chain·응답/입력/공개 파일 변조·권한·원본 코드 변경·새 파일 추가·배타적 발행·재사용·분모 보존을 검증했다. 실제 부모 460개 source와 684개 입력/응답 검증도 통과했다.
- 현재 파생 재채점은 실행 전이다. 앱 v1.16 후보·합성 canary와 전체 회귀 결과는 다음 기록에 추가한다. 운영·학습·release·승격은 변경하지 않았다.
- 체크포인트 검증: 새 진단·앱 후보를 포함한 전체 회귀 925건이 통과했다(86.542초, 건너뜀 0). 최초 실행의 문서 버전 검사 1건 실패는 새 계획과 보존된 부모 실행 계약을 분리해 수정했다. CPU 표적 54건·재집계 dry-run·Ruff·diff 검사 통과 후 진단 검사기·재집계 도구를 먼저 동결하며 실제 재채점과 앱 canary는 별도 산출물로 남긴다.

### 2026-09-15 — S2 실제 비교·재검증 완료와 검사기 오탐 분리

- 구현 기준 `98f44d1`의 `build-c39b4bce5089`를 단일 실행했다. 342요청은 312생성·30사전 차단으로 모두 종료했고 재사용·재시도·오류·미실행 0이다. 312개 모두 EOS 종료, 최대 출력 444 token, 입력 최대 1,692 token·이력 삭제 0이다.
- [공개 집계](../../data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0/build-c39b4bce5089/aggregate.json), [manifest](../../data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0/build-c39b4bce5089/build_manifest.json), [검증](../../data/reports/saju_1b_baseline/system-context-diagnosis/v1.0.0/build-c39b4bce5089/verification.json)을 발행했다. 실행 종료 자동 검증과 별도 `verify --build build-c39b4bce5089` 모두 통과했다. 원출력·trace는 Git 제외 private 경로에 보존한다.
- 필수 사실 자동 PASS는 FULL→MIN에서 K0 3→2, R16 13→6, KI20 0→1(조건별 적용 24)이다. R16 회귀 8개 중 6개는 검사기 오탐으로 확인했다. 이 수치로 모델/정보 조건의 실제 정확도나 우열을 주장하지 않으며 기존 scorer·집계를 사후 변경하지 않았다. 상세 분모·위치 대조·실제 오류·문법 재현은 [완료 기록](../history/2026-09-15-system-context-diagnosis.md)을 따른다.
- 실제 KI20 학습 길이 768과 R16 2,048을 재확인했다. FULL/MIN 모두 48개 중 44개 입력이 768을 넘는다. 학습/serving 차이는 원인 후보이며 크기 효과의 증명이 아니다. 단순 입력 축소만으로 실제 오류가 해소되지 않았고 S3~S6은 미실행이다.
- 실행 후 `.venv/bin/python -B -m unittest discover -s tests -q -b` → 879건 전부 통과·건너뜀 0(81.088초). 운영 v1.14 PID·재시작 0·코드 hash를 유지했고 Phase 6·Runtime release·학습·승격은 변경하지 않았다. 다음 제안은 S1 검사 보강과 새 파생 집계이며 이번 승인 범위는 여기서 종료한다.

### 2026-09-15 — S0/S1 동결·S2 실행 준비

- 전용 config·합성 48개·실제 v1.4/v1.5 adapter 입력과 정정·projection·유한 검사·순차 worker·재개·공개 검증을 구현했다. C_FULL은 v1.15 원본 renderer와 byte/token이 일치하고 P0·부모 이력·필수 사실을 유지한다. C_MIN은 앱 schema를 위조하지 않는 별도 진단 projection이다.
- CPU dry-run은 342요청, 사전 차단 30, 최대 입력 1,692 token, 이력 삭제 0을 확인했다. 승인 2K 해시·2,000행·8축을 다시 검증했다. 부모를 포함한 assistant turn 2,300개 중 3줄 이상 2,051개, 마지막 답변만 보면 기존과 같은 1,751/2,000개다. 새 질문과 허용 학습 질문·공개 20문장의 정확 중복은 0이며 의미 유사도는 미측정이다.
- 현재 v1.15는 `오늘은 사주 얘기를 쉬고 싶어…`를 일진 요청으로 분류해 차단한다. `오늘…내일…` 문장은 다중 날짜로 차단한다. 이 앱 관측은 기대 차단으로 사전 등록해 모델 오답과 분리했다. 이번 실험에서 운영 분류기나 P0를 수정하지 않았다.
- 실제 Claude Code의 추가 코드 검토로 공개 manifest 전체 재계산, 실제 선택 P0 hash, 상속 GPU lock 재확인·부모 PID 결합을 보강했다. 공개 파일에는 집계·hash·manifest만 허용한다. 원응답과 API/저장 결과는 격리 CPU 소비 경로에서 대조하며 실제 운영 브라우저를 검증했다고 표시하지 않는다.
- 상태: S0/S1 `validated`, S2 `implemented`·실제 실행 준비, S3~S6 `not_executed`. 최종 검증 명령·결과와 실제 S2 완료는 아래 후속 기록에 추가한다. 모든 Phase·Runtime 승인·모델·서비스를 유지한다.
- 실행 전 최종 검증: `uvx ruff check scripts tests`, `git diff --check` 통과. `.venv/bin/python -B -m unittest discover -s tests -q -b` → 879건 전부 통과·건너뜀 0. `.venv-data/bin/python -B -m unittest tests.test_system_context_diagnosis tests.test_saju_system_context_plan tests.test_saju_product_roadmap -q -b` → 52건, 실패·오류 0, ML tokenizer 검사 1건은 ML 전체 실행에서 검증했다.
- `validate-contract`, `plan`, ML 환경 `execute` dry-run을 통과했다. GPU 실행 전 입력 identity는 `build-c39b4bce5089`로 고정했으며 이후 응답을 보고 config·사례·projection·scorer를 수정하지 않는다. source hash가 달라지면 해당 build에 추가 생성하지 않는다.

### 2026-09-15 — 실행 전 테스트 기준선 복구

- 사용자 승인 범위는 S0/S1 구현·검증과 S2 비교까지다. 최대 344요청(본 비교 288·보조 48·적격성 최대 8) 안에서 유휴 GPU를 순차 사용하며 S3~S6·서비스 전환·추가 학습은 수행하지 않는다.
- 실제 Claude Code CLI와 테스트 5파일을 교차 점검했다. 과거 LoRA/복구 source pin과 승인 파일은 보존하고, 기능 fixture만 현재 core 또는 실제 임시 source 파일 SHA에 결합했다. 과거 pin 거부·source 변조 거부·날짜 경계 음성 회귀를 추가했다. Claude 제안의 call177 부모 identity 누락은 실제 실행에서 발견해 수정했다.
- 이전 841건의 실패 5·오류 17은 Torch 미설치 9건, LoRA core pin 4건, 복구 source pin 5건, 고정 시계 미주입 4건으로 재분류한다. 이전 통합 기록의 LoRA 13건 전체를 core hash로 묶은 표현은 이 분류로 정정한다.
- 검증: `.venv/bin/python -B -m unittest discover -s tests -q` → 846건 전부 통과, 건너뜀 0. `.venv-data/bin/python -B -m unittest tests.test_mix2k_v4_lora_v1_1 tests.test_mix2k_v4_teacher_recovery tests.test_chart_day_operations tests.test_phase5_dashboard_v1_12 tests.test_phase5_dashboard_v1_13 -q` → 72건, 실패·오류 0, tensor 의존 fixture 건너뜀 9(ML 환경 전체 실행에서 검증).
- 변경한 테스트 5파일 Ruff와 `git diff --check` 통과. 운영 v1.14 PID·재시작 수와 모델·학습 산출물을 유지했다. 새 진단 코드는 별도 체크포인트에서 검증하며 이 기록은 새 GPU 응답 생성 완료를 뜻하지 않는다.

### 2026-09-14 — 기본 브랜치 통합 완료

- PR #28을 `3bb0ce2`로 병합하고 원본 프로젝트 폴더를 `master`에 동기화했다. 자료/서비스 보존 대상 외의 기존 분기와 작업 폴더를 정리했으며 새 분기는 만들지 않았다.
- CPU 표적 58건은 통과했고 원본 폴더 전체 841건에는 기존 계약·복구 fixture·고정 날짜 관련 실패 5·오류 17이 남았다. 상세와 복구 근거는 [통합 기록](../history/2026-09-14-default-branch-integration.md)을 따른다. S0/S1에서 이를 점검하되 S0~S6 실행 완료로 표시하지 않는다.
- 운영 v1.14와 모든 승인 상태를 유지했다. 전역·프로젝트 규칙은 이후 기본 브랜치 직접 작업을 따르며 보존 worktree를 새 작업의 기본 위치로 사용하지 않는다.

### 2026-09-14 — 기본 브랜치 통합 준비

- 사용자 요청으로 기존 최신 브랜치의 PR 병합·기본 폴더 동기화·안전한 정리를 진행한다. 새 브랜치는 만들지 않고 전역·프로젝트 규칙을 기본 브랜치 작업 우선으로 교정했다.
- 이번 작업은 S0~S6 진단 실행이나 운영 배포가 아니다. 통합 전 검증·자료 보존 범위·병합과 정리 결과는 [통합 기록](../history/2026-09-14-default-branch-integration.md)을 따른다.

### 2026-09-14 — 전체 흐름·컨텍스트 진단 설계

- `f34f856` 기반 격리 브랜치에서 입력 용량·컨텍스트 간섭·모델 크기를 구분하고 계산/상태/앱/데이터/학습/검사/운영을 함께 확인하는 S0~S6를 정의했다. 50-A~D와의 연결, 48/24 합성 질문, 680요청 상한, 모델×정보 2×2와 파일 구현 순서를 고정했다.
- 기존 원격 master·v1.14 운영·v1.15 후보의 차이를 읽기 전용으로 확인했다. 다른 세션의 원본 working tree·untracked 파일·실행 service·미완료 보정은 변경하지 않았다.
- 검증 환경은 위 문서 부모의 격리 worktree와 기존 `.venv-data/bin/python`이다. 문서 링크·정본 관계·통제 변수·요청 예산·자동 평가 정책 20건을 통과했다. 초기 단계 표기의 불일치 1건을 수정한 뒤 재검증했다. 이는 문서 계약 검사이며 새 모델 진단 코드의 동작 검증이 아니다.
- 검증 명령: `.venv-data/bin/python -B -m unittest tests.test_saju_product_roadmap tests.test_saju_system_context_plan tests.test_phase6_technical.Phase6TechnicalTests.test_canonical_docs_forbid_person_dependent_gates -v` → 20건 통과. 격리 환경에서는 원본 프로젝트의 interpreter 절대 경로를 사용했다.
- `uvx ruff check scripts tests`, `git diff --check` 통과. `umask 022`에서 `.venv-data/bin/python -B -m unittest discover -s tests -v` → 823건, 실패 4·오류 37·건너뜀 37로 전체 통과는 아니다. 기존 815건 기록과 실패/오류 테스트 이름 41개가 모두 같고 신규 실패는 0이다. 고정 로컬 snapshot/의존성 부재·기존 hash 계약/fixture 문제를 이번 문서 수정으로 해결했다고 기록하지 않는다.
- 전체 테스트 로그 SHA-256은 `d385f9301512765687031ddf9de2e2f5a5fc5e560f2c0e22e952ab11302bc481`, 비교한 기존 로그는 `23eb8b8f8e234651b8a4e0bafb4ba551cb324d29979abbb6218668d22bdb034d`다. 로그 자체는 공개하지 않는다. S0에서 실행 환경의 선행 실패를 재분류하고 비교 자격에 영향이 있는 항목부터 해소한다.
- 새 진단 코드·GPU 생성·학습·병합·배포는 실행하지 않았다. 기존 질문 문서·versioned config/report·모델·학습 데이터는 변경하지 않았다.
- 다음 작업은 S0/S1의 계약·trace·CPU 회귀 구현이다. 큰 모델은 아직 등록하지 않았고 모델 크기·컨텍스트·데이터 중 어느 하나를 원인으로 확정하지 않았다.
