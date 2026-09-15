<!-- 2026-09-15-phase7-canonicalization.md - ZIP 계획 전량 보존과 Phase 7~14 정본화·검증 기록. -->

# 2026-09-15 — Phase 7 문서 정본화

## 작업 범위와 기준

- 사용자 승인 범위는 `saju_plans_20260915.zip`의 모든 내용을 반영한 Phase 단위 계획과 문서 정합화다. 번호는 사용자 선택대로 완료 Phase 0~6을 보존하고 Phase 7~14로 이어간다.
- 작업 시작 `master`·upstream·원격은 `390ca88f4b65f6c3b87f0f36ca26cbc492251a06`이다. 초기 변경은 사용자 ZIP 미추적 파일뿐이며 ZIP을 변경·추적하지 않는다. 새 브랜치·worktree를 만들지 않는다.
- 시작 시 기존 worktree·세션·index를 확인했다. 운영 `saju-mix2k-r16-dashboard-v1-14.service`는 PID 3144071, NRestarts=0, commit `0e77621846c4e9894cb40d801e84d59ad57cb0de`로 유지한다. 이번 작업에서 서비스·GPU 실행·모델 다운로드·teacher 호출·학습을 수행하지 않는다.

## 변경 내용과 판단

- [원문 반영표](../plans/saju_product_roadmap/source-20260915.md)에 ZIP SHA와 문서 3개의 원문을 보존한다. 전체 841행·80개 절·32개 링크를 유지하고 비공백 588행을 각각 하나의 담당 Phase·명시적 anchor·처리 방식에 연결한다. 이는 588개의 독립 기능이라는 뜻이 아니라 원문 누락을 탐지하기 위한 행 단위 색인이다.
- [실행 로드맵](../plans/saju_product_roadmap/README.md)과 Phase 7~14를 작성하고 현재 기준선, 최상단 01·02·03, 초기 baseline 인덱스, 계산기·모델 진단·LoRA·조건부 데이터/학습 계획을 같은 순서로 연결한다.
- 계산 범위 확대는 동결하되 실제 결함 유지보수는 허용한다. 자료 날짜·동적 요청 날짜·연결 날짜, 부분 원국의 제한, 격리 50개 입력 분과 ±50분 창의 차이를 명시한다.
- 앱 오차단 8A와 R16 지시문 묶음 S3/8B를 분리한다. 8B→9/S4는 앱 전체 변경 완료를 기다리지 않는다. P1은 prompt 파일뿐 아니라 formatter 안내를 포함하고, S4는 공통 P0의 K0·3B FULL/MIN 교차 비교다.
- Phase 10 연결 상태·응답 모드·직접 조회·정정 이력, Phase 11 실제 teacher fallback·7축·400건 처리, Phase 12 새 질문·세 층의 자동 평가, 조건부 Phase 13 단일 학습·14 제한 운영으로 나눈다. S6를 후속 학습 자료나 검증 근거로 재사용하지 않는다.
- 원문의 새 Phase/Gate를 만들지 않는 취지는 실행 권한 불변으로 유지하면서 사용자 선택에 따라 번호만 이어 붙인다. 원문의 문서 작성만으로 commit/push하지 않는 문장은 이번 명시 요청과 저장소 자동 저장 규칙에 맞춰 문서 검증 후 한국어 저장으로 조정한다. GPU·학습·배포 권한은 여전히 별개다. 대응 행과 이유는 JSON에 기록한다.
- 총 예산 680, 완료 부모 요청 342, 남은 S3/S4/S6 336과 공유 적격성 2를 구분한다. 문서 정본화는 해당 실행 예산을 승인하지 않는다.

## 검증

- ZIP CRC와 각 멤버/보관 파일 byte 비교: 3/3 일치. ZIP SHA-256 `85fe0f0716347b5fdbe83fe9b1aeccca67efd6917b30de2dfb248592b8bab47c` 불변. 자동 테스트는 ZIP이 없는 checkout에서도 고정 원문 SHA를 검증한다.
- `.venv-data/bin/python -B -m unittest tests.test_saju_product_roadmap tests.test_saju_system_context_plan tests.test_saju_phase_plans tests.test_phase6_technical.Phase6TechnicalTests.test_canonical_docs_forbid_person_dependent_gates -q -b`: 32개 통과. 신규 11개 테스트는 원문·588행 전량 연결·80절·링크·명시적 anchor·번호·권한과 누락/중복/잘못된 경로 거부를 검사한다.
- 첫 문서 검사에서 8건 실패했다. 7개 Phase의 읽기용 원문 색인 역링크와 Phase 7의 공통 자동 평가 원칙이 빠져 있어 보완했다. 기존 평가 정책을 완화하지 않았으며 재실행 32개 통과로 확인했다.
- `uvx ruff check scripts tests`: 통과. `git diff --check`: 통과.
- `.venv/bin/python -B -m unittest discover -s tests -q -b`: 전체 947개 통과, 92.877초. 데이터용 환경의 문서 검사와 ML 의존성이 있는 전체 회귀 환경을 구분했다. 정식 모델 생성·학습은 실행하지 않았다.
- 원문 핵심 조건을 내용 단위로 추가 대조했다. 계산 재개 기준·독립 기대값, 지시문 묶음·기대값 동결, 같은 P0/정밀도·계보 한계, 실제 teacher·400건 builder 계약, 새 질문 누수 금지, 앱 요청 전량 분모·운영 분리를 확인했다. 행별 연결 통과만으로 의미 반영이 증명된다고 주장하지 않는다.
- 저장 전 원격 `master`는 기준 `390ca88`과 같고 index에는 다른 변경이 없었다. 운영은 PID 3144071·NRestarts=0·active/running으로 불변이다. 변경은 문서·문서용 JSON·정합성 테스트뿐이며 사용자 ZIP은 미추적 그대로 보존한다.
- 분리 함수의 읽기 전용 재현에서 `오늘 야근했어`, `어제 영화 보고 왔어`, `내일 면접이라 긴장돼`, `이번 주에 운동 시작했어`는 현재 v1.16의 `period_request`였다. 기존 25개 CPU canary 통과와 별개인 추가 회귀 후보이며 이번에는 수정·서비스 요청을 하지 않는다.

## 남은 작업

- Phase 7의 문서·정합성 검증을 완료했다. 다음 실행은 [Phase 8](../plans/saju_product_roadmap/phases/phase-08.md)이고 이번에는 착수하지 않는다.
- 3B는 첫 후보로 계획에 명시했을 뿐 정확한 revision·환경 등록과 다운로드는 미실행이다. 새 데이터 생성·학습·운영 전환은 결과와 별도 실행 결정이 필요하다.
- 원시 응답·출생정보·모델·AI Hub 제한 자료·sealed blind는 열거나 추가하지 않는다. 과거 생성 보고서와 고정 config·release·prompt·학습 build는 변경하지 않는다.

## 진행 기록

- 2026-09-15: 원문 보존·전량 대응·정본 연결·전체 회귀 검증을 완료하고 기본 브랜치의 한국어 저장 체크포인트로 정리했다. 다른 세션 변경이나 기존 산출물은 포함하지 않는다. 후속 Phase·GPU·학습·운영 권한은 변경하지 않는다.

<a id="supplement-20260915"></a>
## 2026-09-15 — Phase 7 보완 정본화

### 범위와 상태

- 기준은 `d151548e2eed37e8d36dd2e5fd9328aec1fb7315`의 원본 `master`다. 시작 index·추적 변경 없음, 사용자 ZIP만 미추적이다. 다른 세션의 cwd·process와 기존 worktree를 확인했으며 새 분기·worktree·전역 설정 변경은 없다.
- 기존 완료 기록은 위에 보존한다. 이번 보완은 문서·문서용 반영표·정합성 테스트만 수정했으며 Phase 7의 보완 정본화·검증을 완료했다. 문서 반영 완료를 후속 기능 구현 완료로 표시하지 않는다.
- 운영은 dashboard v1.14 PID 3144071·NRestarts=0·active/running을 유지한다. Phase 8 이후는 계획만 등록하며 모델 다운로드·GPU 생성·데이터 보정·teacher 호출·학습·운영 교체를 실행하지 않는다.

### 사용자 보완 요구와 반영 위치

| 항목 | 반영 위치 | 대조할 조건·예외·검증 |
|---|---|---|
| 1. 정본 권한·검사 범위 | [AGENTS](../../AGENTS.md), [원문 계약](../plans/saju_product_roadmap/source-20260915.md) | 초기 인덱스는 완료 계약, 로드맵은 전체 순서; 활성 하위 문서 재귀 검사·archive 원문 보존 검사를 분리 |
| 2. 문서/실행 상태 | [반영표](../plans/saju_product_roadmap/requirements-20260915.json), [Phase 7](../plans/saju_product_roadmap/phases/phase-07.md#source-governance) | 의미 유형·반영 방식·문서 상태·실제 상태; 588행을 신규 과제로 간주하지 않고 조건·예외까지 대조 |
| 3. 8A/10 경계 | [8A](../plans/saju_product_roadmap/phases/phase-08.md#routing), [10](../plans/saju_product_roadmap/phases/phase-10.md#revision-history) | 같은 후속의 사주/일반/무맥락 회귀; 읽기 전용 참조와 입력/이력 변경 분리; 8B→9 독립 진행 |
| 4. 학습 결정 연결 | [11](../plans/saju_product_roadmap/phases/phase-11.md#failure-attribution), [12](../plans/saju_product_roadmap/phases/phase-12.md#training-decision), [13](../plans/saju_product_roadmap/phases/phase-13.md#training-entry) | 가설·조건부 명세→실제 결과 대조→별도 실행; 앱/지시로 해소되면 건너뜀·소비 질문 재사용 금지 |
| 5. 후보 사전 동결 | [12 확인](../plans/saju_product_roadmap/phases/phase-12.md#confirmation) | 전체 실행 구성·채택 규칙 동결; 동일 질문 재선발 금지·미시험 조합 첫 확인·응답 종류별 분모 |
| 6. 요청 예산 | [예산](../plans/saju_product_roadmap/phases/phase-07.md#budget) | 680-342=338=336+2; CPU/실제 호출 구분·추가 호출과 학습 후 평가 별도 예산 |
| 7. 운영 비교·되돌림 | [기준선](../plans/saju_product_roadmap/phases/phase-14.md#comparison-baselines), [복귀](../plans/saju_product_roadmap/phases/phase-14.md#rollback) | R16/P0/C_FULL과 KI20 구분·미비교 미측정·비교군 추가 금지·호환 구성과 세션/binding 복귀 |

### 원문 대조와 반영표

- 원문 3개·841행·80절·비공백 588행과 ZIP·각 원문 SHA는 보존한다. 반영표 스키마만 1.1.0으로 확장하며 원문 기준 commit과 ID·행 번호를 바꾸지 않는다.
- `kind`는 형식, `requirement_type`은 주된 의미, `disposition`은 반영 방식이다. `documentation_status`와 `execution_status`는 독립이다. 현재 사실·정책·예시·근거·구조는 실행 대상 아님, 인용된 완료 이력은 과거 완료, 실제 문서 작업과 미래 실행 과제는 별도로 구분한다.
- M-0294·M-0296·O-0175·O-0181의 원문은 보존한다. 학습 결정 시점에 관한 사용자 보완을 `adapted`와 이유로 기록하고 기존 예산/순서 절 대신 Phase 11·12·13의 실제 조건 절에 연결한다.
- 내용 대조에서 추가 32행의 넓은 연결을 실제 조건의 소유 절로 좁혔다. 동결/유지보수, S3 통제와 지시문, 모델 등록/비교, projection 통합, 후보 확인/운영 판단을 구분했다. 원문·ID·행 수·실제 실행 상태는 그대로이며 섹션 색인 C-0066도 동결 절로 맞췄다. 이는 새 요구나 실행 과제 추가가 아니다.
- 번호·저장 권한의 기존 조정 3행도 유지한다. 조건부 요약 오염 검사는 원문대로 보존하되 첫 Phase 10 후보의 자동 요약은 제외한다. 소비 질문 재사용·새 비교군 확대·실행 권한을 완화하지 않는다.
- 588행의 조건·예외·금지·종료 기준 대조를 마쳐 문서 상태를 `verified`로 확정했다. 실행 상태는 문서 작업 완료 21행·원문이 인용한 과거 완료 16행·미실행 31행·조건부 미실행 42행·실행 대상 아님 478행으로 구분한다. 이는 기능 개수가 아니며 미실행 과제를 문서 완료로 승격하지 않는다.

### 보완 검증

- ZIP CRC·SHA-256·각 멤버와 archive 바이트 비교: 3/3 일치. 원문 841행·80절·32링크·비공백 588행과 원본 해시는 불변이다. 보존 원문의 과거 표현을 현재 정책 검사에 맞춰 고치지 않았다.
- `.venv-data/bin/python -B -m unittest tests.test_saju_product_roadmap tests.test_saju_system_context_plan tests.test_saju_phase_plans tests.test_phase6_technical.Phase6TechnicalTests.test_canonical_docs_forbid_person_dependent_gates -q -b`: 41개 통과. 활성 하위 문서 재귀·절/링크·권한·조건·예산·상태의 음성 사례까지 검사한다.
- 첫 보완 문서 검사 40개 중 1개는 `documentation_status=pending`을 완료 상태로 인정하지 않아 실패했다. 원문 조건 대조와 연결 보완 후 `verified`를 확정했고, 세부 연결 회귀 1개를 더한 41개가 통과했다. 완료 조건이나 원본 해시 기대값을 완화하지 않았다.
- 보존 확인 보조 명령의 첫 실행은 Python 3.10에 없는 `hashlib.file_digest` 사용으로 실패했다. 저장소 변경 없이 호환되는 `hashlib.sha256`으로 재실행해 동일 SHA·CRC·바이트 검사를 통과했다. 환경 호환 오류이며 원문 손상이나 검증 생략이 아니다.
- `uvx ruff check scripts tests`: 통과. `git diff --check`: 통과.
- `.venv/bin/python -B -m unittest discover -s tests -q -b`: 전체 956개 통과, 93.436초. ML 의존성을 갖춘 기존 환경에서 회귀만 수행했으며 실제 모델 생성·학습은 하지 않았다. 위의 947개 결과는 보완 전 이력이고 이번 결과와 구분한다.
- 저장 전 원격 `master`와 로컬 기준은 `d151548`로 같고 다른 index 변경은 없었다. 코드·설정·공개 보고서·P0·보관 원문·Phase 0~6 개별 문서의 Git diff는 없다. 초기 인덱스는 완료 계약을 유지하고 전체 순서 안내만 보완했다.
- 운영 PID 3144071·NRestarts=0·active/running 및 기존 worktree는 불변이다. 새 브랜치·worktree나 서비스 변경 없이 문서·문서용 JSON·정합성 테스트 25개 파일만 명시적으로 저장한다. 사용자 ZIP은 미추적 그대로 보존한다.

### 다음 작업과 남은 제한

- 다음 착수 대상은 Phase 8A 오차단 보완·8B S3다. 이번 보완에서는 구현·생성을 수행하지 않는다. Phase 9는 8B 뒤 진행하고 8A·10 완료를 기다리지 않는다.
- 학습 필요성·모델 우열·운영 대비 개선은 아직 결정되지 않았다. Phase 11 명세만으로 학습하지 않으며 Phase 12 실제 결과와 별도 실행 결정이 필요하다.
