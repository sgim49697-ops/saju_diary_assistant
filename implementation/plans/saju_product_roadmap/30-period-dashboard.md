<!-- 30-period-dashboard.md - 일별 기간 release를 dashboard v1.12에 안전하게 연결하는 절차를 정의한다. -->

# 30. Dashboard v1.12 기간 범위

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
