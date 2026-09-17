<!-- 2026-09-17-phase10-product-fixes.md - 부모 검증을 보존한 제품 후보 버그 보완과 자동 회귀 기록. -->

# Phase 10 제품 후보 버그 보완

<a id="phase10-fixes"></a>
## 범위와 판단

시작 `master`는 `276bb8d`이며 기존 추적 파일·index 변경은 없고 사용자 ZIP만 미추적이었다. 새 브랜치·worktree를 만들지 않는다. 재점검에서 4개 버그를 재현했으며 과거 1,121개 테스트 통과가 해당 경계의 검증 완료를 뜻하지 않았음을 기록한다.

- 늦은 응답이 새 대화/새 원국을 덮는 문제: 화면 변경 번호·요청 소유권으로 성공·오류·finally와 조회 결과를 제한한다. 이전 서버 응답의 정상 저장은 보존하고 자동 재시도하지 않는다.
- 일진 후속이 원국 설명으로 바뀌는 문제: 이전 응답 유형·선택 사실을 읽기 전용 참조한다. 새로운 일반 문장 작성 요청은 앞선 사주와 분리한다.
- 연주·일간신문 등 오인: 필드 경계·조사·명리 맥락을 검사하고 유일한 단순 사실 조회만 직접 답한다.
- 신강/신약 판정 누락: 개인 계산은 차단하고 용어 설명·제외 표현·분리 가능한 일반 과제를 구분한다.

수정 후보는 [v1.19](../../scripts/training/phase5_dashboard_v1_19.py), 정책 v1.1.0, schema 1.9.0, 포트 8771이다. 세션은 `dashboard/v1.19.0/product-v1.1.0/manual_sessions`로 분리한다. 모델/adapter·지시문 원문·기존 세션·운영 v1.14와 계산 release는 변경하지 않았다. feature 기본 off이며 실제 사용은 Phase 12의 별도 확인 대상이다.

## 공개 증거

- 새 [aggregate](../../data/reports/saju_1b_baseline/dashboard-product-canary/v2.0.0/build-35dc83ce161d/aggregate.json), [manifest](../../data/reports/saju_1b_baseline/dashboard-product-canary/v2.0.0/build-35dc83ce161d/build_manifest.json), [verification](../../data/reports/saju_1b_baseline/dashboard-product-canary/v2.0.0/build-35dc83ce161d/verification.json).
- 집계 SHA `fa305426a4c7e21addf04068dc8436c297e7582b66079f6d0c53c688ebb75891`, manifest SHA `e27e58796dc3e9c2bee866a4cf4d15cc132d9617ee0fa15b2923b456fd4707d1`.
- CPU 56개, 데스크톱/모바일 합성 화면 32개, 고정 정책 사례 18개. 모의 생성과 실제 CLI의 비모델 경로만 실행했다. 모델 생성 0, 누적 631/680·잔여 49 유지, 의미·자연스러움은 미측정이다.
- 부모 v1.18과 8A/S3/S4의 코드·산출물은 수정하지 않았다. 새 canary는 부모 manifest를 고정한다.

## 검증 명령과 결과

`SAJU_PLAYWRIGHT_MODULE`과 `SAJU_CHROMIUM_EXECUTABLE`에는 기존 설치 경로를 지정했다. 새 MCP·운영 서비스는 기동하지 않았다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.dashboard_product_canary_v2 execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_product_canary_v2 verify --build build-35dc83ce161d
.venv-data/bin/python -B -m scripts.evaluation.dashboard_product_canary verify --build build-f13715ee1d91
uvx ruff check scripts tests
.venv/bin/python -B -m unittest discover -s tests -q -b
git diff --check
```

첫 화면 회귀에서 앞 장면의 숨겨진 삭제 버튼을 누르는 테스트 구성 오류가 발생했다. 정상 재계산으로 삭제 가능한 화면을 만든 뒤 지연 삭제 경쟁을 재현하도록 고쳤으며 기대 조건을 완화하지 않았다. 최종 통합 검증 결과는 아래 진행 기록에 남긴다.

## 진행 기록

- 2026-09-17 최종 검증: 새 canary 실행·재검증과 부모 v1.18·8A·S3·S4 재검증 모두 통과했다. 문서 41개, 전체 `.venv` unittest 1,178개(142.534초, 건너뜀 0), Ruff·JavaScript 문법·diff 검사를 통과했다. `.venv-data`의 문서/CPU 검사와 ML 의존성이 설치된 `.venv`의 전체 검사를 구분한다. 운영 PID 3144071·active/running·재시작 0·GPU 계산 프로세스 없음이다. 다른 세션의 `2026-09-17-worktree-cleanup.md`와 사용자 ZIP은 이 커밋에 포함하지 않는다.
- 2026-09-17: 4개 버그 수정과 CPU 56개·합성 화면 32개를 검증했다. 다음은 기존 데이터만 읽는 Phase 11이며 생성·보정·학습·승격·sealed blind 접근은 금지 범위를 유지한다.
