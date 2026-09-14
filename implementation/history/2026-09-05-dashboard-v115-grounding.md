<!-- 2026-09-05-dashboard-v115-grounding.md - v1.15 입력 조건·날짜 차단·사실 검사와 합성 대화 재진단 결과를 기록한다. -->

# v1.15 연결 대화 후보 구현·재진단

## 결론

원본 K0 tokenizer 공유, 생성 전 날짜 범위 확인, 실제 Runtime schema의 역할별 사실 검사까지 구현했다. 최종 신규 합성 재생은 30개 모델 요청 중 27개 생성·3개 사전 차단으로 완료했고, 첫 turn 6개 그룹에서 K0·R16·KI20 입력 token이 모두 일치했다. 현재 날짜의 실제 HTTP 연결도 통과했다.

이는 비교 조건과 진단 경로의 검증이다. 모델의 설명·입력 이해·문장 작성 품질 승인이 아니다. 기존 v1.14 서비스·기본 모델 선택·Phase 6·Runtime release·학습·모델 승격은 변경하지 않았다. 후보 병합과 서비스 전환도 수행하지 않았다.

실행 정본은 [v1.15 계획](../plans/dashboard_v1_15_grounding.md), 공개 산출물은 [집계](../../data/reports/saju_1b_baseline/dashboard-v115-realistic-chat/v1.0.0/build-9e7b7d39475b/aggregate.json), [HTTP canary](../../data/reports/saju_1b_baseline/dashboard-v115-realistic-chat/v1.0.0/build-9e7b7d39475b/http_canary.json), [검증 요약](../../data/reports/saju_1b_baseline/dashboard-v115-realistic-chat/v1.0.0/build-9e7b7d39475b/verification.json), [build manifest](../../data/reports/saju_1b_baseline/dashboard-v115-realistic-chat/v1.0.0/build-9e7b7d39475b/build_manifest.json)에 보존했다.

## 구현 경계

- 세 모델 모두 원본 K0 tokenizer 파일·effective backend·chat template을 검증하고 `fix_mistral_regex=False`로 로드한다. 생성 직전 렌더링 원문 SHA와 최종 token ID SHA, 입력 길이를 기록한다. 후속 turn은 각 모델의 이전 답변만 사용한다.
- 다른 날짜는 `RUNTIME_DATE_REBIND_REQUIRED`, 주·월·연 등 여러 날 범위는 `RUNTIME_PERIOD_SCOPE_UNSUPPORTED`, 불명확한 날짜는 `RUNTIME_DATE_SELECTION_REQUIRED`로 GPU 호출 전에 차단한다. 서버 KST 시계를 사용하며 HTTP client의 시계 override는 거부한다. 자연어에서 날짜를 계산·재결합하지 않는다.
- 원국 질문과 기간 질문, 일반 대화, 쉬운 풀이를 분리했다. 전체 기둥은 전체 나열 요청에서만 요구한다. 실제 `period.hard_facts.period`의 연·월·일 간지와 날짜를 검사하고, 한자·한국어·명시적 부정·교정을 회귀 사례로 검증한다.
- 검사기는 `saju-bound-chart-grounding-v2.0.1`이다. 실제 KI20 출력에서 발견한 한국어 천간 한 글자를 일주로 말한 오류와 지시문 재출력도 경고한다. 유한 label 문법의 검사이므로 모든 자연어 주장의 완전한 판정을 보장하지 않는다.
- 지원 범위의 원출력은 경고가 있어도 바꾸지 않는다. 보정 재작성·retry는 0회다. 새 세션은 별도 버전·경로에 보존하고 초기 검사 버전의 세션을 수정하지 않는다.
- `--artifact-root`로 기존 모델·run을 검증해 읽었다. 모델을 복사하거나 학습 산출물과 기존 대화를 덮어쓰지 않았다.

## 최종 합성 재생 결과

한 합성 원국을 포함한 6개 대화 흐름·10개 질문의 작은 진단이다. 아래 통과 수는 연결 응답의 구조 사실 검사이며 정확도·사용성 점수가 아니다. 비연결 응답 5개씩은 원국 grounding 집계 대상이 아니다.

| 모델 | 생성 | 사전 차단 | 연결 응답 구조 검사 | 최대 입력 token | 제외 turn |
|---|---:|---:|---:|---:|---:|
| K0 | 9/9 | 1 | 1/4 | 1,986 | 0 |
| R16 | 9/9 | 1 | 4/4 | 1,787 | 0 |
| KI20 | 9/9 | 1 | 0/4 | 1,802 | 0 |

- R16은 원국과 날짜 간지를 구분했고, 쉬운 조언과 일간 확인에 불필요한 누락 경고가 발생하지 않았다. 다만 장점 설명을 원국 사실 나열로 대체하고, 명시된 시간 미상을 유지하지 못하며, 요청한 발송용 문장 대신 행동 지시를 쓰는 문제가 남았다.
- K0는 날짜 간지를 원국 기둥으로 바꾸는 오류가 남았다. KI20은 입력 재요구, 지시문 재출력, 잘못된 사실·문장 작성 문제가 남았다. tokenizer 통일만으로 모델 문제가 해결된 것은 아니다.
- 최대 입력은 4,096 상한 안이며 제외 turn은 0이다. 이번 실패를 context 용량 부족으로 설명할 근거는 없다. 자동 계약 밖의 자연스러움·해석 의미 품질은 `not_measured`로 유지한다.
- 최종 재생 응답 27개는 앞선 두 디버깅 재생의 해당 응답과 모두 byte 단위로 같았다. 검사·진단 격리 수정이 원출력을 재작성하지 않았음을 확인했다.

## 실제 HTTP 검증

별도 임시 loopback server에서 HTML과 화면 메타데이터 6개 API, 새 합성 Runtime session, 원국 계산, 현재 KST 날짜 단일 일진 계산, R16 생성 순으로 확인했다. 시계를 고정하지 않았고 당시 서버 날짜는 2026-09-05였다.

실제 R16 생성 1회 후 내일·주간 후속 요청은 각각 두 사전 차단 code를 반환했다. 생성 runner 호출은 총 1회로 유지됐다. 입력 token ID와 응답은 고정 재생의 동일 R16 요청과 일치했다. 임시 server와 lease를 닫고 생성한 합성 Runtime session을 삭제했다. 실패했던 첫 준비 시도의 합성 session도 해당 진단 저장소에서만 확인해 삭제했다. 기존 사용자 session은 정리 대상으로 삼지 않았다.

브라우저 픽셀·상호작용 전체 검증을 수행했다고 주장하지 않는다. 이번 검증은 실제 HTTP 경로·자산 응답·JavaScript 구문과 GPU 생성 연결이다. 기본 8767 서비스는 동일 process에서 HTTP 200을 유지했다.

## 디버깅·실행 차수

1. 초기 scorer: 27개 생성·3개 차단. 한국어 한 글자 천간의 역할 검사 누락을 발견했다.
2. scorer v2.0.1: 새 build에서 27개 생성·3개 차단. HTTP 초기 준비는 정상 `201 Created` 기대값 오류로 생성 없이 중단했고, 수정 후 HTTP 생성 1회·차단 2회를 완료했다.
3. 진단 `umask` 복구 보강 후 최종 build: 재생 27개 생성·3개 차단, 실제 HTTP 생성 1회·차단 2회. 최종 재개는 추가 생성 0회, private manifest 불변이었다.

이번 구현 turn의 GPU 생성 총량은 83개다. 최종 진단 집계는 28개이며, 디버깅 중 생성한 55개는 별도 경로에 보존하되 최종 모델 집계에서 제외했다. 이전 진단의 응답 38개는 이 총량에 포함하지 않는다. 새 학습이나 봉인 데이터 생성·재사용은 없었다.

## 검증과 남은 상태

- 관련 회귀·변조·재개·HTTP·기존 v1.14·LoRA·문서 정책 47건 통과. `uvx ruff check scripts tests`, `node --check scripts/training/phase5_dashboard_assets/v1.15.0/dashboard.js`, `git diff --check` 통과.
- 전체 `.venv-data/bin/python -m unittest discover -s tests -q`: 805건, 실패 4·오류 37·skip 37. 변경 전 clean worktree의 782건에서도 같은 기존 실패 4·오류 37·skip 37을 확인했다. artifact 미배치, Torch 없는 data 환경, 기존 LoRA core hash와 recovery target 계약 문제가 남아 있으므로 전체 green이 아니다.
- 전체 회귀 중 진단의 `umask`가 공개 fixture 권한 검사에 영향을 주는 문제를 추가로 재현하고 복구했다. 최종 전체 실행에서는 해당 추가 오류가 사라졌다. 실패를 감추기 위해 비공개 원본을 복사하거나 기존 학습 계약을 수정하지 않았다.
- Python 3.10.12, Torch 2.13.0, Transformers 4.57.6, PEFT 0.20.0, Tokenizers 0.22.2, Skyfield 1.55 환경을 사용했다. 공식 Ubuntu 개발 헤더의 지정 SHA를 확인해 Git 제외 로컬 sysroot를 사용했다. native JIT는 활성 상태다. 기존 모델의 RoPE 설정 경고는 기록하되 불변 가중치·config를 수정하지 않았다.
- 최종 private artifact SHA·0600 권한과 실행 코드 fingerprint를 재검증했다. 공개 산출물에는 원문·출생 입력·Runtime ID·키를 넣지 않았다.
- 다음 범위는 R16의 입력 유지·요청 수행 보정과 기존 비공개 artifact 의존 회귀의 환경/계약 정리다. 이번 결과만으로 새 데이터 생성·학습·승격·서비스 전환을 시작하지 않는다.
