<!-- 30-period-dashboard.md - 일별 기간 release를 dashboard v1.12에 안전하게 연결하는 절차를 정의한다. -->

# 30. Dashboard v1.12 기간 범위

이 문서는 2026-09-02 완료 구현의 계약·재현 이력이다. 아래 v1.11 process 보존은 당시 작업 범위이며 현재 운영 상태는 [00 기준선](00-current-baseline.md)을 따른다. 후속 실행 순서는 [로드맵 README](README.md)의 50-A부터이며 이 후보를 자동 재실행·배포하지 않는다.

## 동작

- 기존 네 runtime/session route를 유지한다.
- 사용자는 구조화된 오늘·내일·주말·이번 주·이번 달·직접 날짜 control만 사용한다.
- deterministic fact table을 모델과 무관하게 먼저 표시한다.
- `이 원국·기간으로 새 대화 시작`을 눌러야 snapshot이 새 대화에 결합된다.
- 날짜나 원국이 바뀌면 기존 binding을 해제하고 새 대화를 요구한다.
- compact chart+period context가 입력 상한 3,584 token을 넘으면 fact UI는 유지하고 모델 연결만 차단한다.

## 운영 경계

- dashboard v1.11 파일과 운영 process를 그대로 둔다.
- v1.12는 새 config·assets·진입점·별도 feature flag로 만든다.
- flag 기본값은 off다.
- 실제 운영 교체는 별도 port의 HTTP·GPU canary 이후에만 판단한다.

## 자동 Canary 200건

feature off 10, day/상대 날짜 40, 1~31일 범위 30, label 경계 30, process restart 20, 보안·변조·rate 30, 지원 밖 10, K0·KI20 동일 context 10, 공개 누출 20으로 고정한다.

Gate는 HTTP 오류 0, snapshot swap 0, fabricated period fact 0, 로그·보고서 개인정보/ID 0이다. 공개 산출물은 aggregate와 build manifest뿐이다.

## 진행 기록

### 2026-09-05 — 완료 이력과 현재 운영 포인터 구분

- 당시 v1.11 보존 문구를 현재 service 상태로 오해하지 않도록 00 정본을 연결했다. 기존 계약·canary·후속 운영 승인 경계는 보존했다. 문서 검증 결과는 [재정렬 기록](../../history/2026-09-05-model-cause-roadmap.md)을 따른다.

### 2026-09-02 — v1.12 구현·자동 canary 완료

- dashboard v1.11은 수정하지 않고 v1.12 config, versioned assets, 별도 Python 진입점과 `saju-period-dashboard-binding-v1.2.0`을 추가했다.
- 기존 AES-256-GCM session의 exact 원국을 process 재시작 뒤 재검산하고, 승인된 `saju-period-daily-label-release-v1.0.0-59e326f8f086`만 기간 요청에 사용한다. 공개 snapshot에는 원국 allowlist와 일별 기간 allowlist만 남긴다.
- 여섯 구조화 기간 control, 모델 독립 날짜별 표, 명시적 새 대화 결합, 원국·기간 변경 시 binding 해제, K0·KI20 동일 canonical context와 3,584 token 입력 상한을 유지했다.
- 자동 canary `build-ae2d73958afe`에서 feature off 10, 상대 날짜 40, 직접 범위 30, label 경계 30, process 재시작 20, 보안·변조·rate 30, 지원 밖 10, 동일 context 10, 공개 누출 20의 합계 200/200을 통과했다.
- 검증 명령은 `uvx ruff check ...`, `node --check scripts/training/phase5_dashboard_assets/v1.12.0/dashboard.js`, 관련 unittest 42건, `period_dashboard_canary_v1 verify`, `git diff --check`다.
- v1.12 feature는 기본 off이며 현재 v1.11 운영 process와 port를 교체하지 않았다. 별도 port 실제 GPU canary와 제한 활성화 판단은 후속 운영 단계이고, strict/full Runtime·sealed blind·학습·모델 승격 권한은 변하지 않았다.
