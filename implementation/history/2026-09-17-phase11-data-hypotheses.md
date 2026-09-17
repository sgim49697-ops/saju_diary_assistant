<!-- 2026-09-17-phase11-data-hypotheses.md - S5 CPU 전수 대조·학습 가설과 조건부 명세의 실행·검증을 기록한다. -->

# Phase 11 데이터 대조·조건부 학습 가설 완료

<a id="phase11"></a>
## 변경 범위와 판정

2026-09-17, 원본 `master`의 v1.19 버그 수정 `8753dcb` 이후 S5 분석기·고정 입력 계약·CPU 회귀·공개 세 파일과 관련 정본/반영표를 추가했다. [Phase 11](../plans/saju_product_roadmap/phases/phase-11.md)의 **분석·가설·명세만 완료**하며 데이터 보정·학습 실행이나 품질 승인이 아니다. 기존 v1.18·v1.19 canary, S3/S4 점수·응답·scorer·P0, 학습 입력·checkpoint, Phase 6·계산 release·소비된 sealed blind는 보존했다.

실제 R16의 2K build `build-54836f556b4f`·`train-f340a82c76d3`, spec/teacher/token 감사·기존 비봉인 개발 200행·adapter bytes·tokenizer pin을 대조했다. 별도 보정 `repair-23340fc31022`는 기존 worktree의 state만 읽고 시작/종료 hash를 검증했다. 경로를 추측하거나 복제·수정·재개하지 않는다. 누락·변조·symlink·source 변경은 실패로 처리한다. tokenizer만 CPU에서 로드하고 모델 가중치는 로드하지 않았다.

## 확인된 사실과 제한

- 기존 token/mask/EOS 감사 2,000행 재현 일치. 동일 대화의 학습/serving token ID·mask 2,000행 동일, 잘림·loss 누출·EOS 비감독 0. 최대 rendered 1,960/상한 2,048. 입력 직렬화 결함을 찾지 못했지만 컨텍스트 간섭·작은 모델 한계를 배제하지 않는다.
- 공감 250행은 모두 원국 미연결·단일 턴, 후속 300행은 원국 연결·이전 assistant 한 턴. 최종 답변 1,751/2,000행이 비공백 3줄 이상이다. 지정 표현 검색 0건은 의미 커버리지 부재의 증명이 아니다.
- 개발 200행의 정규화 마지막 질문은 학습 질문과 전량 겹친다. 부모 대화·chart fingerprint 일치 0이며 전체 샘플 중복이나 정답 유출로 표현하지 않는다. 질문 일반화 근거로 쓸 수 없고 의미 계열 중복은 미측정이다. 기존 split은 변경하지 않았다.
- 실제 초안 Codex 2,000, 최종 판정 Codex 분리 1,809·Claude 191. 재작성 143행, 결정적 초안 검사 실패 75회 중 claim 문법 73회. 자동 판정 시도 PASS 2,187·FAIL 116. 시도·행 분모를 분리하고 실패 코드만으로 오탐을 확정하지 않는다.
- 보정 400건은 accepted 238·needs_review 3·needs_draft 159·기존 provider 호출 55회로 보존. intake 250·uncertainty 100·짧은 QA 50을 교체해 1,600+400=2,000행이며 현재 R16에는 미반영이다. 연결 일반 대화/전환을 직접 겨냥하지 않으므로 Phase 12 전에는 보류한다.
- 7개 행동의 관련 데이터 축·S3/S4 PASS/FAIL/UNSCORABLE·앱 수정·반증 조건을 [담당 절](../plans/saju_product_roadmap/phases/phase-11.md#behavior-axes)에 연결했다. 후보의 실제 개선은 미측정이며 정상 입력에서도 오류가 남고 데이터 필요성이 확인될 때만 별도 보정/학습한다.
- [조건부 명세](../plans/saju_product_roadmap/phases/phase-11.md#failure-attribution)는 고정 K0에서 새 r16 LoRA, 잠정 교체 2K·2,048 상한·학습률 5e-5·1 epoch·batch 8·seed 42, split/중단/재개·별도 새 평가 조건이다. 실행 권한은 false다. 앱·지시문으로 해소되면 건너뛴다.

## 공개 근거와 재현

- [aggregate](../../data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8/aggregate.json): SHA-256 `bb34e74c7f0dda2b58b1d1c6b4fa868d1ac0c58a2cdca35bbe48312f19ed4150`
- [build manifest](../../data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8/build_manifest.json): SHA-256 `65ef8dd8b4647766d98b5adacf826e1647aae34ed32e9fa3b707510bb3929f7d`
- [verification](../../data/reports/saju_1b_baseline/system-context-s5/v1.0.0/build-27a91a8e21a8/verification.json): `verified`. 개별 행 ID·근거는 Git 제외 `private/system-context-s5/`의 0600 파일에만 보관한다.

`SAJU_REPAIR_ROOT`는 기존 로컬 보정 target의 검증된 절대 경로를 작업자가 지정하는 변수다. 공개 보고서에 로컬 절대 경로를 기록하지 않는다. 아래 명령은 모델/teacher 호출을 하지 않으며 고정 입력이 없거나 달라지면 실패한다. CUDA 비활성·로컬 tokenizer 전용/offline 설정에서 실행했다.

```bash
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -B -m scripts.evaluation.system_context_s5 validate-contract --repair-root "$SAJU_REPAIR_ROOT"
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -B -m scripts.evaluation.system_context_s5 plan --repair-root "$SAJU_REPAIR_ROOT"
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -B -m scripts.evaluation.system_context_s5 execute --repair-root "$SAJU_REPAIR_ROOT"
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -B -m scripts.evaluation.system_context_s5 verify --build build-27a91a8e21a8 --repair-root "$SAJU_REPAIR_ROOT"
```

위 계약·dry-run·실행·재검증은 통과했다. 입력 계보·불변 발행·중복/비유한 JSON·공개 누출·행 분모·loss mask 변조·fallback 집계 CPU 회귀 18개를 통과했다.

## 진행 기록

- 2026-09-17 최종 검증: `.venv-data/bin/python -B -m unittest tests.test_system_context_s5 -q` 18개, `tests.test_saju_phase_plans tests.test_saju_product_roadmap tests.test_saju_system_context_plan` 52개 모두 통과했다. 반영표 588행 중 M-0294·O-0175의 실행 상태/근거만 바뀌었으며 유형·원문·문서 반영 상태는 불변이다.
- CUDA 비활성·offline 환경의 `.venv/bin/python -B -m unittest discover -s tests -q -b` 전체 **1,199개 통과, 148.215초, 실패·건너뜀 0**. ML 의존성이 필요한 전체 검사와 `.venv-data`의 CPU/문서 검사를 구분했다.
- `uvx ruff check scripts tests`, v1.19 JavaScript와 브라우저 canary의 `node --check`, `git diff --check` 모두 통과했다. v1.19 canary `build-35dc83ce161d`를 다시 검증해 CPU 56개·합성 화면 32개·기존 aggregate/manifest hash를 재현했다. S5 `build-27a91a8e21a8`도 전수 재집계·공개/비공개 근거 대조를 통과했다.
- v1.19 재검증 첫 명령은 Playwright/Chromium 환경변수 누락으로 실행 전에 실패했다. 사용법·환경 지정 오류이며 기존 manifest와 hash가 같은 로컬 설치본을 명시해 성공했다. 검사 기준이나 동결 source를 완화하지 않았다. 새 브라우저·MCP 설치/기동은 하지 않았다.
- 운영 서비스는 PID 3144071·active/running·재시작 0을 유지했고 GPU 계산 프로세스는 없었다. private 근거는 디렉터리 0700·파일 0600·Git 제외를 확인했으며 공개 세 파일에 원시 질문/응답·행 ID·로컬 절대 경로가 없음을 확인했다.

## 남은 범위와 보존

다음은 Phase 12의 **전체 구성 하나 동결 → 새 질문 계열 분리 → 계약/CPU dry-run → 별도 승인된 실제 확인**이다. 새 24개는 만들거나 소비하지 않았고 S6 실행 권한을 자동 열지 않았다. 실제 모델/teacher 호출 추가 0, 누적 요청 631/680·잔여 49(본 비교 48+여유 1)이며 학습 후 평가는 별도 예산이다.

운영 v1.14·기본 KI20, feature off·Runtime release·모델 승격·학습 상태는 바꾸지 않는다. 다른 세션의 worktree 정리 기록과 사용자 ZIP은 이 커밋에서 제외한다. 사람 판정이나 의미 품질 미측정을 새 완료 조건으로 추가하지 않는다.
