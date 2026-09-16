<!-- 2026-09-16-phase9-s4-audit.md - S4 구현 재점검·안전성 보완과 실제 모델 비교 미실행을 기록한다. -->

# Phase 9 / S4 구현 재점검

## 범위와 현재 판단

사용자의 구현 전체 확인·설명·보완 요청으로 원본 `master`의 `23cf1247676416c9443d4bfb3930008ffe230f00`에서 시작했다. 최근 [S4 구현](2026-09-16-phase9-s4.md)의 계약·모델 등록/수집·입력 구성·실행/재개·채점/집계·검증·회귀를 대조했다. 기존 Phase 8 산출물은 별도 검증하고 저장소 전체 unittest로 회귀를 확인한다. 저장소의 모든 과거 코드 행을 새로 감사했다는 뜻은 아니다.

**실제 비교 미실행, GPU 생성 0**이다. 실행기 수정과 CPU 검증은 모델 성능 증거가 아니다. 3B 다운로드·실제 dry-run·VRAM 적격성·192요청 비교는 실행하지 않는다. Phase 9의 실제 비교 과제는 `not_executed`, 계획상 잔여는 **242 = S4 192 + S6 48 + 공유 적격성 2**다. 질문·P0·facts·부모 이력·scorer v1.2 판정 규칙과 공식 등록 pin은 바꾸지 않았다.

## 구현을 읽는 순서

1. `system_context_s4_contracts.py`: 동결 S2/S3와 48개 질문, 두 모델·FULL/MIN·P0, 호출/권한 상한을 확인한다.
2. `system_context_s4_models.py`: 두 모델의 실제 파일과 각각의 tokenizer/template를 검증한다. 3B 수집은 별도 명시 명령만 허용한다.
3. `system_context_s4_projection.py`: 같은 의미의 입력을 각 tokenizer로 렌더링한다. 모델 간 token ID 동일성 대신 질문·지시문·facts·이력 동일성, 무삭제, 입력 위치를 검사한다.
4. `system_context_s4.py`와 backend: 192요청 중 20개는 동결 사전 차단, 최대 172개는 생성한다. 두 모델 최장 적격 입력부터 검사하고 GPU lock 아래 요청별 process로 실행한다. 불확실한 요청은 재시도하지 않는다.
5. `system_context_s4_scoring.py`와 scorer v1.2: 조건별 PASS/FAIL/UNSCORABLE, 대응쌍과 네 조건 공통 판정 가능 분모를 집계한다. 실패와 판정 불가를 합치지 않는다.
6. `verify`: private 입력·응답·종료 증거에서 공개 aggregate/manifest를 다시 계산한다. 공개 파일만 검증하며 원응답은 Git에 올리지 않는다.

K0와 3B는 구조·학습 과정도 다르므로 파라미터 수만의 순수 인과 비교는 아니다. 저장/API 재생은 기존 K0 슬롯의 CPU 검사이며 3B 앱 연결이 아니다. latency는 요청별 모델 로딩을 포함하는 cold 비용이고 warm 비용·자연스러움·전체 의미 품질은 미측정이다. 이를 새 사람 평가 Gate로 전환하지 않는다.

## 발견한 문제와 보완

| 문제 | 원인과 수정 | 회귀 범위 |
|---|---|---|
| 미등록 파일이 실제 loader를 바꿀 수 있음 | 등록 파일 hash만 확인하면 단일 가중치·adapter/tokenizer 덮어씌우기를 놓친다. 3B와 K0 모두 root 파일 허용 목록, 보조 디렉터리의 symlink/특수 파일 검사를 적용하고 3B에 `use_safetensors=True`를 명시했다. | 미등록 가중치·adapter·tokenizer·Python 파일 거부, 허용 cache·보조 자료 유지 |
| 실패 후 작성된 응답을 정상 재개로 오인 | 부모가 확인한 종료 코드·timeout 여부와 시작/응답/로그 hash를 불변 종료 증거로 남긴다. 정상 종료 증거가 없거나 변조되면 재사용하지 않는다. | 응답 작성 후 exit 7, timeout 직후 exit 0, 종료 증거 누락·변조·권한 위반 |
| 중단·원장 손실로 실행/예산 경계가 흔들림 | 자기 worker에 TERM→KILL 후 wait하고 lock을 반환한다. 시작·종료·로그 어느 흔적이든 남으면 자동 재생성하지 않는다. 기존 build가 있는데 원장이 없으면 자동 초기화하지 않는다. | 강제 종료 승격, 흔적만 남은 요청, 원장 삭제, 없는 build 재개 |
| 잘못된 명령 조합을 무시 | 실제 실행은 확인한 `--build`가 필수다. 잘못된 `--resume`·`--execute`·worker 전용 인자는 준비/수집 전에 차단한다. | 인자 오류 시 prepare·download·execute 호출 0 |
| 자기모순 비용/종료 기록 | 실제 마지막 token·출력 길이로 EOS/max-token 종료를 대조하고 NaN/무한 시간·bool token·잘못된 메모리 관계를 거부한다. | 종료 사유·token·비유한 시간 음성 사례 |
| retokenize의 상세 위치 기록 누락 | P0·runtime·selected_data의 문자/token 위치를 각 tokenizer로 복구했다. 원문과 메시지는 바꾸지 않는다. | 모의 서로 다른 tokenizer, 실제 K0 입력 96개·상세 구간 264개 |
| 재개 횟수 의미가 불명확 | 공개 manifest에 `new_generations`·`reused_generations`·`new_preblocks`·`reused_preblocks`를 분리하고 private 요청 ID와 대조한다. aggregate의 생성 수는 build 전체의 고유 응답 수임을 명시한다. | 부분 재개: 기존 생성 2 + 새 생성 170 + 새 사전 차단 20; 완료 build 재개는 발행 증거 불변 |
| 보호 검사가 실행/발행 뒤에만 있음 | raw Git 추적은 실행·발행·검증 진입 때 거부하고 운영 서비스 상태는 매 요청 직전 확인한다. | raw 추적/서비스 변경 시 추가 생성 0 |

수정 전 기존 관련 47개는 통과했지만 새 음성 테스트 4개 메서드의 **15개 실패 사례**가 위 결함 일부를 재현했다. 기존 테스트 통과만으로 안전하다고 판단하지 않았으며 새 검사를 완화하지 않고 구현을 보완했다. scorer 규칙을 결과에 맞춰 바꾸거나 기존 S3 결과를 재채점하지 않았다.

이번 수정 전 실제 S4 raw/public build가 없었다. 따라서 아직 실행되지 않은 S4 코드와 테스트를 보완했으며 source fingerprint와 이후 build ID는 새 코드로 계산한다. 기존 S2/S3와 8A 승인 산출물, 공식 3B 등록 파일과 S4 비교 계약은 덮어쓰지 않는다.

## 검증 명령과 결과

```bash
.venv-data/bin/python -B -m scripts.evaluation.system_context_s4 validate-contract
.venv-data/bin/python -B -m scripts.evaluation.system_context_s4 plan
.venv-data/bin/python -B -m scripts.evaluation.system_context_s4 download
.venv-data/bin/python -B -m unittest tests.test_system_context_s4 tests.test_system_context_s4_audit tests.test_system_context_scoring_v1_2 -q -b
.venv-data/bin/python -B -m unittest tests.test_saju_phase_plans tests.test_saju_product_roadmap tests.test_saju_system_context_plan -q -b
uvx ruff check scripts tests
.venv/bin/python -B -m unittest discover -s tests -q -b
git diff --check
```

- 실제 K0 tokenizer를 오프라인 CPU로 사용해 동결 부모 입력 **96개**, P0/runtime/selected_data의 상세 구간 **264개**를 원본과 대조했다. token ID와 구간 시작/끝이 일치하며 최대 입력은 1,692 token이었다. 모델 생성은 0이고 3B tokenizer 검증은 아니다.
- `verify_k0(prepare_context())`로 실제 K0의 loader 파일 허용 목록과 **8개 파일 hash**를 검증했다. 3B 검증을 통과시켰다는 뜻은 아니다.
- 관련 CPU **66개**, 문서 **44개/0.381초**, 전체 ML 환경 unittest **1,064개/103.965초**가 통과했다. 실패·오류·건너뜀 0이며 Ruff·`git diff --check`도 통과했다. 계약·192요청 plan·수집 계획은 모두 `gpu_used=false`, `artifact_writes=false`였다. CPU mock을 실제 GPU 검증으로 기록하지 않는다.
- 실제 오프라인 `.venv/bin/python -B -m scripts.evaluation.system_context_s4 execute` dry-run은 **exit 1 / blocked**였다. `S4 등록 snapshot 디렉터리가 없습니다.`로 정상 중단했으며 다운로드·GPU·S4 build 생성은 없었다. 이는 실제 dry-run 통과가 아니라 미수집 상태의 fail-closed 확인이다.
- S3 `build-ffd985905b51`은 오프라인 `system_context_s3 verify`로 재검증했다. aggregate `46e83c1e1b787ad1a244b723f3982efa275d042fe04e4cc1ed6d2590368bf3a2`, manifest `c1e83a8423da5e74a5f9c0eed5de0b7eeca6c851fde7893f4eca3d0122c8e5a0`가 그대로다.
- 8A `build-49b9aed70565`도 고정 Playwright/Chromium 경로를 지정한 `dashboard_intent_canary_v2 verify`로 재검증했다. aggregate `262de303d52054fbbbb677d8a77adc5f16c4768b5cf5350bc361f4fd805b82c7`, manifest `69ec9feeee97e597d5cd4ce1bfd5887b41ecf2b04b34ee4de5f19eda90346223`가 그대로다. 새 화면/GPU 실험이 아니라 기존 근거 재검증이다.
- 운영 서비스는 active/running·PID 3144071·재시작 0을 유지했다. `git fetch origin master` 후 기준 `23cf124`와 원격 차이 0을 확인했고, 기존 세션·worktree·사용자 ZIP을 보존했다.

## 남은 제한과 다음 실행

3B 실제 파일과 tokenizer, BF16 로딩, 긴 입력 생성·종료, 여유 VRAM·실제 비용·모델 출력 품질은 미검증이다. 코드는 이를 확인할 준비가 된 것이며 “큰 모델이면 해결”이라는 결론은 아직 없다. [기존 실행 순서](2026-09-16-phase9-s4.md#next-execution)에 따라 수집·실행 범위를 확인한 뒤 진행한다. 학습·데이터 보정·앱 운영 교체·Phase 10 이후를 자동 실행하지 않는다.

## 진행 기록

- 2026-09-16: S4 실행/재개·파일 무결성·입력 추적·집계·CLI 음성 회귀를 보강했다. 관련 66개·문서 44개·전체 1,064개·Ruff·diff·S3/8A 재검증을 통과했다. 기존 계산 release·학습 checkpoint·P0·소비된 sealed blind·운영 서비스·다른 세션·사용자 ZIP은 변경하지 않았다. 검증된 명시적 코드·테스트·문서만 원본 `master`의 한글 체크포인트로 저장·푸시하며, 실제 3B 비교는 미실행 상태로 남긴다.
