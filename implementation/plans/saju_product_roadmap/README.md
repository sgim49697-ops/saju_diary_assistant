<!-- README.md - 사주 제품 후속 workstream의 정본 우선순위와 실행 순서를 정의한다. -->

# 사주 제품 후속 로드맵 정본

| 항목 | 값 |
|---|---|
| 문서 버전 | `saju-product-roadmap-v1.0.0` |
| 구현 기준 | `master@2298573ec2a2a14e6ae003ca45bccdeda54ade37` |
| 기준일 | 2026-09-02 |
| 현재 Runtime | `saju-runtime-release-v1.5.0-8b1d6ea2d46e` |
| 현재 앱 | dashboard v1.11, exact 원국+단일 일진 제한 운영 |
| 다음 실행 축 | 기간 계약·복원 → 일별 범위 → 앱 → 단일 날짜 관계 |

이 디렉터리는 새로 반입된 전체 구조·Runtime/기간/대시보드·모델/데이터 계획을 현재 저장소 사실에 맞춰 재분할한 실행 정본이다. 기존 문서와 역할은 다음처럼 나눈다.

1. 과거 구현·계산 권위와 불변 보고서는 [`../saju_runtime_calculator_adoption.md`](../saju_runtime_calculator_adoption.md)가 소유한다.
2. Phase 0~6과 현재 모델 판정은 [`../saju_1b_10k_20k_baseline/README.md`](../saju_1b_10k_20k_baseline/README.md)가 소유한다.
3. MIX20K-v3.0.1 후보의 감사 결과는 [`../mix20k_v3_repair_plan.md`](../mix20k_v3_repair_plan.md)가 소유한다.
4. 이 디렉터리는 위 사실을 바꾸지 않고 앞으로 실행할 workstream과 Gate만 소유한다.

## 현재 판정

- v1.4 과거 공식 원국과 이를 부모로 한 v1.5 KST 정오 단일 일진은 승인됐다.
- conformance v10 `build-46185262164f`는 8,522/8,522일 공식 label mismatch 0이다.
- dashboard v1.11은 명시적 원국·날짜 대화 연결과 자동 Grounding Gate까지 완료됐다.
- 주·월 범위, 분 단위 미래 절입, 원국×기간 관계는 승인되지 않았다.
- Phase 6은 `AUTOMATED_REPAIR_REQUIRED`로 완료됐고 의미 품질은 `not_measured`다. 계약 밖 평가를 완료 조건으로 추가하지 않는다.
- 소비된 sealed blind는 `spent_completed`이며 다시 열거나 재사용하지 않는다.
- MIX20K-v3.1 생성·추가 학습·모델 승격은 계속 금지한다.

## 실행 파일 순서

| 순서 | 문서 | 초기 상태 | 완료 조건 |
|---:|---|---|---|
| 00 | [`00-current-baseline.md`](00-current-baseline.md) | 완료 | 통합 audit가 v1.5·v1.11까지 검증 |
| 10 | [`10-period-contract-and-restore.md`](10-period-contract-and-restore.md) | 완료 | 기간 v2 계약·원국 재검산 복원 통과 |
| 20 | [`20-daily-range-runtime.md`](20-daily-range-runtime.md) | 완료 | 8,522일·263,717 window mismatch 0 |
| 30 | [`30-period-dashboard.md`](30-period-dashboard.md) | 완료 | dashboard v1.12 자동 canary 통과 |
| 40 | [`40-day-relation-runtime.md`](40-day-relation-runtime.md) | 미시작 | 단일 날짜 relation 자동 전수 Gate 통과 |
| 50 | [`50-automatic-model-evaluation.md`](50-automatic-model-evaluation.md) | 대기 | 동일 context K0↔KI20 자동 비교 완료 |
| 60 | [`60-mix20k-v3-1-build.md`](60-mix20k-v3-1-build.md) | 차단 | 새 build·비학습 preflight blocker 0 |
| 70 | [`70-training-and-promotion.md`](70-training-and-promotion.md) | 차단 | 별도 승인된 MIX2K 진단 이후 판단 |

00은 기준선 정합화이고 첫 신규 기능은 10이다. 50 이후는 10~40의 승인 산출물을 입력으로만 사용하며 선행 실행하지 않는다.

## 공통 불변조건

- 기존 v1.4·v1.5 release, conformance v9·v10, dashboard v1.11 파일을 수정하지 않는다.
- 미래 절입의 정확한 물리 시각을 확정하지 않는다. 첫 기간 release는 공식 대조된 날짜별 label만 제공한다.
- 모델은 출생정보에서 원국·기간·관계를 계산하지 않는다.
- 내부 ID·HMAC key·출생 원문·원시 모델 출력은 공개 보고서와 로그에 넣지 않는다.
- 새 기능은 기본 off이고 자동 canary만으로 운영 service를 교체하지 않는다.
- 자동 계약이 없는 주관적 품질은 `not_measured`로 남기며 Phase blocker로 사용하지 않는다.

## 진행 기록

### 2026-09-02 — 반입 계획 정본화 설계

- 반입 문서의 기준 commit `6927eab`, runtime v1.4, dashboard v1.9 상태를 현재 `2298573`, runtime v1.5, dashboard v1.11로 교정했다.
- 완료된 단일 일진을 다시 구현하지 않고 기간 계약·범위·앱·관계를 별도 Gate로 재분할했다.
- 누락된 이미지 참조는 제거하고 저장소에서 검증 가능한 Mermaid·표·계약만 유지했다.
- Phase 6·sealed blind·MIX20K-v3.0.1·KI20 checkpoint 권한은 변경하지 않았다.

### 2026-09-02 — 현재 기준선 audit v1.1 구현

- 과거 audit v1.0 동작과 현황 v1.3 snapshot은 보존하고 Runtime v1.5 release·conformance v10·dashboard v1.11을 검증하는 audit v1.1을 추가했다.
- 프로젝트 현황 v1.4 `build-faf55ff6886d`를 발행해 제한 원국·단일 일진 운영과 strict/full·v3.1·학습 차단을 별도 Gate로 표시했다.
- Ruff와 상태·audit 표적 unittest 19건, 현황 registry 재현, `git diff --check`를 통과했다.
- 기존 private build는 내용을 복제하지 않고 격리 worktree에 read-only hardlink해 v1.1 quick audit을 통과했다. sealed blind payload는 열지 않았고 GPU·학습·tracked write는 모두 false였다.

### 2026-09-02 — 기간 계약·원국 복원 완료

- 공개 `saju-period-request-v2`는 여섯 날짜 표현과 explicit ISO 범위만 받고 chart ID·timezone·reference clock·policy·release·revision 주입을 거부하도록 고정했다.
- 서버 KST 기준 today·tomorrow·주말·이번 주·이번 달과 최대 31일 명시 범위를 결정론적으로 해석하고 과거·연간·2049년 이후를 차단했다.
- 암호화 session의 exact 출생 slot으로 v1.5 원국을 다시 계산해 chart ID·입력·공개 facts·source hash를 대조하는 내부 authorization을 추가했다.
- Ruff, 계약 CLI, 표적 unittest 10건과 실제 DE440s·새 engine process 복원 후 단일 일진 실행을 통과했다. 공개 request·resolve 결과에는 opaque Runtime ID나 출생값을 넣지 않았다.

### 2026-09-02 — 일별 label 범위 Runtime 승인

- 기간 Runtime registry v1.1과 공개 일별 label 결과 계약, release-domain HMAC, 승인 release 소비 엔진을 구현했다.
- conformance v11 `build-cd8eaaf50792`에서 공식 8,522일을 275회로 분할 계산하고 길이 1~31일의 연속 window 263,717개를 전수 구성했다. label·authority·순서·중복·누락 mismatch는 모두 0이었다.
- `saju-period-daily-label-release-v1.0.0-59e326f8f086`을 write-once registry로 발행했다. feature는 기본 off이며 strict/full Runtime, production 앱 연결, sealed blind, MIX20K-v3.1, 학습·승격 권한은 열지 않았다.
- Ruff, 기간·원국 Runtime unittest 19건, conformance report 재검증, release 재검증과 `git diff --check`를 통과했다. 공개 보고서에는 원시 일별 행·private path·키를 기록하지 않았다.

### 2026-09-02 — Dashboard v1.12 기간 연결 완료

- 기존 dashboard v1.11 파일과 실행 process를 유지하고 v1.12 config·assets·진입점·기간 binding을 새 버전으로 추가했다.
- 사용자는 오늘·내일·주말·이번 주·이번 달·최대 31일 직접 범위만 구조화 control로 요청한다. 날짜별 label 표는 모델과 무관하게 먼저 표시하며 `이 원국·기간으로 새 대화 시작`을 눌러야 canonical snapshot이 결합된다.
- 자동 canary `build-ae2d73958afe`는 계획된 9개 stratum 200/200을 통과했다. HTTP unexpected error, snapshot swap, 기간 fact transport mismatch와 공개 private 누출은 모두 0이고 실제 암호화 session을 새 engine에서 20회 재개했다.
- K0·KI20에는 동일 context를 전달하며 입력 3,584 token 상한은 유지한다. feature 기본 off, 운영 service 교체, GPU 생성, strict/full 승인, sealed blind, MIX20K-v3.1, 학습·승격은 모두 수행하지 않았다.
- Ruff·Node 구문 검사, v1.10·v1.11 회귀 포함 관련 unittest 42건, canary verifier와 `git diff --check`를 통과했다.
