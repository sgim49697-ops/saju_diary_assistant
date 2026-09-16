<!-- 2026-09-16-phase9-s4-execution.md - S4 모델 수집·실제 dry-run과 첫 요청 중단을 성공한 비교와 구분한다. -->

# Phase 9 / S4 실제 실행 기록

## 승인 범위와 준비 결과

사용자가 모델 추가 테스트 계획의 실행을 요청해 원본 `master`의 `7e7819eb2ff1c95fb358da757cbe66173743d130`에서 시작했다. 범위는 K0/3B × FULL/MIN·공통 P0의 192요청 비교이며, 학습·데이터 보정·운영 교체·새 24문항·sealed blind 접근은 제외한다. [Phase 9](../plans/saju_product_roadmap/phases/phase-09.md)의 기존 질문·scorer·생성 조건을 바꾸지 않았다.

- `git fetch origin master` 뒤 원격 차이 0·추적/index 변경 0을 확인했다. 기존 worktree·세션·사용자 ZIP은 보존했다.
- RTX 5070 Ti의 계산 process 없음·여유 VRAM 15,176MiB를 확인했다. 운영 v1.14는 active/running·PID 3144071·재시작 0이었다.
- 기존 Python 3.10.12·Torch 2.13.0+cu130·Transformers 4.57.6과 검증된 local Python 헤더를 사용했다. native JIT를 끄거나 패키지를 변경하지 않았다.
- 공식 3B revision `6a5d7889964c4c590299d16e309eabab1f73f8a9`의 **10파일·7,028,155,860바이트**를 수집하고 등록 크기·SHA-256 전부를 검증했다. 이어 오프라인 `verify-model`로 K0 8개·3B 10개 파일을 재검증했다. 모델은 Git 제외 상태다.
- 실제 오프라인 `execute` dry-run이 `build-296dffd1ef51`, 192요청·예정 차단 20·최대 입력 1,692 token으로 통과했다. 두 모델 tokenizer·동결 입력 재구성 검증이며 실제 생성 성공과는 다르다.
- 실행 전 관련 CPU 66개/11.262초·전체 unittest **1,064개/126.172초**·Ruff가 통과했다. 기존 S3·8A 별도 `verify`도 불변 공개 hash로 통과했다. 아래 실제 실패는 이 테스트가 잡지 못한 소비 경로 연결 결함이다.

<a id="blocked-run"></a>
## 실제 실행 중단과 보존 증거

**상태: blocked — 첫 K0 요청의 저장 계약 충돌, 비교 미완료.**

`SYSTEM_CONTEXT_S4=K0_KANANA3B_P0_V1`과 오프라인 환경·검증된 `CPATH`에서 `execute --execute --build build-296dffd1ef51`을 단일 실행했다. 첫 요청 `request-001`의 worker 종료 코드가 1이었고 timeout은 아니었다. 실행기는 오류 1건을 남기고 중단했다. 실패 요청을 재생성하거나 새 build로 우회하지 않았다.

| 구분 | 확인 값 |
|---|---:|
| 계획 요청 | 192 |
| 시작/오류 요청 | 1 / 1 |
| 저장·검증된 생성 응답 | 0 |
| 실제 처리한 사전 차단 | 0 |
| 미실행 요청 | 191 |
| 3B GPU 실행 요청 | 0 |

worker는 생성 뒤 저장/API 소비 단계에 도달했지만 그 단계에서 실패해 정상 응답·telemetry를 보존하지 못했다. 모델의 응답 품질·정확도·생성 속도 수치로 사용할 결과가 없다. 실제 모델 실패와 실행기 연결 결함을 구분한다.

- 동결 prepared SHA-256: `657f2b9936fae3978b54e69b40a41f68a2c25d41dda8a07b1aa2f2a5083d0082`.
- 실패 response SHA-256: `8e3c75b7c5d306ba639fadf73fc101e7227426390e7efb88f8b7ce46f02622e6`.
- worker log SHA-256: `cd5407cbd8d046ec4284b301f10a6763a115d92060ac149a1e3185ac030f9db4`.
- private 시작·종료 증거와 `active-build.json`의 원래 192요청 등록을 보존했다. 원문·로그·모델은 Git에 추가하지 않는다.
- 공개 aggregate/build manifest/verification은 **미발행**이다. 별도 `verify --build build-296dffd1ef51`도 공개 manifest 부재로 exit 1 / blocked였으며 성공으로 표시하지 않는다.

## 원인과 CPU 분리 재현

S4의 `input_identity()`는 `s4-k0_instruct-official-v1.0.0` 또는 `s4-kanana3b_instruct-official-v1.0.0`라는 tokenizer revision을 만든다. 반면 기존 v1.15 저장 검사는 `k0-original-tokenizer-v1.0.0`만 허용한다. S4 backend가 결과를 그대로 기존 CPU 소비 함수에 넘겨 저장 검증에서 `Phase5DashboardError`가 발생했다.

추가 모델 로딩·GPU 생성 없이 합성 문장 하나로 실제 저장/API 소비 함수를 재생했다. 나머지 입력을 고정하고 revision만 바꾼 세 조건에서 **S4 K0 차단 / S4 3B 차단 / 기존 canonical 대조 통과**를 확인했다. 대조의 원문·저장·표시 변경은 없었다. 이는 원인 분리 재현이지 수정 완료나 3B 앱 검증이 아니다. 초기 재현 명령의 상대 경로 사용 오류는 절대 경로로 고쳐 재실행했으며 모델 호출 예산을 소비하지 않았다.

기존 CPU 시험은 worker 응답과 소비 성공을 모의 값으로 구성해 이 실제 계약 충돌을 놓쳤다. 전체 테스트가 통과했다는 이유로 연결이 정상이라고 판단할 수 없다. 로그의 기존 K0 RoPE 경고와 이번 tokenizer revision 거부도 구분하며 불변 모델 config를 변경하지 않는다.

## 예산·다음 결정

이 절은 **첫 중단 당시의 결정 대기 기록**이다. 이후 사용자 재진행 승인과 v1.1 수정·별도 예산 사용은 [복구 기록](2026-09-16-phase9-s4-recovery.md)에 이어서 남긴다. 아래 실패 산출물과 당시 검증 결과는 변경하지 않는다.

실패 1요청도 보수적으로 소비 처리한다. 계획 산술 잔여는 **241 = 680 - (438 + 1) = 기존 S4 미처리 191 + S6 48 + 여유 2**다. 기존 191건은 실패한 build의 미처리분이지 자동 재개 권한이 아니다.

실패 산출물을 보존하고, 소비 계약 호환성 수정·실제 CPU 소비 회귀를 먼저 검증한 새 버전의 전체 192요청을 다시 실행하려면 목적·범위·추가 1요청을 별도로 확인해야 한다. 여유 2 중 1 사용을 선택할 경우 완료 후 잔여는 49가 되지만, 이 기록만으로 재실행을 승인하거나 예산을 전용하지 않는다. 기존 raw·원장 삭제, 같은 build의 코드/응답 수정, 검사 완화는 금지한다.

Phase 9는 완료가 아니다. Phase 10 이후·학습·운영 변경은 진행하지 않으며 새 사람 평가 조건도 추가하지 않는다.

## 최종 검증과 정본 반영

```bash
.venv-data/bin/python -B -m unittest tests.test_saju_phase_plans tests.test_saju_product_roadmap tests.test_saju_system_context_plan -q -b
uvx ruff check scripts tests
.venv/bin/python -B -m unittest discover -s tests -q -b
git diff --check
```

- 관련 문서 **46개/0.450초**, 전체 unittest **1,066개/113.476초**, Ruff·diff 검사가 통과했다. 테스트 실패·오류·건너뜀 0은 회귀 결과이며 위 실제 실행 실패를 해소했다는 뜻이 아니다.
- 문서 검사 중 기존 Phase 9의 `상태: 미실행` 고정 기대가 실제 중단 상태와 충돌해 1개 실패했다. Phase 9만 정확한 build·1오류/191미실행·중단 증거를 요구하도록 갱신했고 Phase 10 이후 미실행·조건부 보호는 유지했다. 증거 삭제·허위 완료·실제 시작을 없던 일로 만드는 상태 변경을 거부하는 회귀를 추가했다.
- 반영표 v1.3.0은 실행 상태 `blocked`를 추가해 M-0293·O-0173에 중단 증거를 연결했다. 원문 3개의 바이트/해시·588행 대응·문서 반영 완료와 기존 Phase 8 완료 증거는 그대로다. 새로운 Phase나 별도 관리 체계를 만들지 않았다.
- 기존 S3·8A를 최종 재검증했고 공개 aggregate/manifest 해시는 실행 전과 같았다. 실패 S4의 `code_sha256` 전체가 현재 소스와 일치하고 prepared/response/log 해시도 위 값 그대로임을 확인했다. 실험 코드·모델 pin·scorer·P0는 수정하지 않았다.
- 운영 서비스 PID 3144071·재시작 0을 유지했고 S4 worker 종료 뒤 GPU 계산 process는 없다. 모델·실패 raw가 Git 추적에 포함되지 않았음을 확인했다. 검증된 문서·정합성 테스트만 한국어 커밋·푸시 대상이다.

## 진행 기록

- 2026-09-16: 모델 수집·파일 검증·실제 dry-run을 완료했으나 첫 K0 요청의 소비 계약 오류로 중단했다. 1오류·191미실행·공개 집계 미발행을 기록하고 CPU 대조로 원인을 확인했다. 문서 46개·전체 1,066개·Ruff·diff·부모 산출물 재검증을 통과했다. 후속 수정/재실행 범위 확인 전 추가 모델 호출·실패 산출물 변경·운영 전환은 하지 않는다.
