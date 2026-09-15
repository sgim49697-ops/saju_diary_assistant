<!-- 2026-09-15-dashboard-v116-intent.md - 운영 교체 없는 앱 의도 정책 후보와 CPU canary 결과를 기록한다. -->

# Dashboard v1.16 의도 정책 후보 검증

실행 정본은 [전체 흐름 진단 계획](../plans/saju_system_context_diagnosis.md)이다. [S2 파생 재집계](2026-09-15-system-context-rescore.md)와 별도의 앱 후보이며, 원래 사전 차단 30개를 재생성하거나 새 허용 결과로 변경하지 않았다.

## 변경 범위

- 후보 코드: [`phase5_dashboard_v1_16.py`](../../scripts/training/phase5_dashboard_v1_16.py).
- 설정: [`phase5-dashboard-v1.16.0-intent-candidate.json`](../../configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.16.0-intent-candidate.json).
- 정책: [`dashboard_grounding_v3.py`](../../scripts/training/dashboard_grounding_v3.py), `saju-request-intent-v1.0.0`, `saju-bound-chart-grounding-v3.0.0`.
- 부모 v1.15는 모듈 전역·version 분기·자기 파일을 호출하는 worker 구조이므로 독립 version snapshot으로 분리했다. 전역 monkeypatch wrapper를 사용하지 않는다. 정규화 회귀로 version·포트·경로·정책 import 이외의 후보 본체와 config가 부모와 같음을 검증했다.
- 기본 포트 8769, 새 private 세션 `dashboard/v1.16.0/grounding-v3.0.0/manual_sessions`. UI는 v1.15 자산을 그대로 재사용한다.
- 기본 모델 KI20, P0·공통 tokenizer·입출력 4,096/4,096·원응답 보존·재시도 0·기능 기본 off 유지. 운영 v1.14와 새 후보의 실행/세션 경로를 혼합하지 않는다.

## 의도와 날짜 안전

| 합성 입력 유형 | 후보 처리 |
|---|---|
| 오늘 힘들었어 / 오늘은 사주 얘기를 쉬고 싶어 | 일반 대화; 오래된 일진 날짜 때문에 차단하지 않음 |
| 운세를 제외한 메시지 작성 / 내일 날씨 질문 | 일반 대화; 운세 사실을 의무적으로 요구하지 않음 |
| 사주 말고 내일 운세 / 그냥 내일 운세를 메시지로 | 별도로 남은 운세 요청은 날짜 검사 실행 |
| 그럼 내일은 어때? / 그럼 내일이 어때? | 보수적 기간 요청; 재연결 또는 날짜 선택 필요 |
| 오늘 힘들었어. 그럼 내일은 어때? | 다중 날짜 제한 유지, 범위 미지원 차단 |
| 잘못된 날짜·연도 없는 날짜·주간 요청 | 기존 차단 유지 |
| 일반 대화에 변조된 원국 snapshot 첨부 | 의도와 무관하게 무결성 검사에서 차단 |

명시적으로 제외한 주제만 지우고, 별도의 능동 운세 요청을 먼저 판정한다. 날짜 추출 `_DATE/_RELATIVE/_RANGE`와 KST/snapshot 규칙, 앱의 사실 label 파서·근거 권한은 변경하지 않았다. 예를 들어 `내일이`는 새 의도 정책에서 기간으로 분류하지만 기존 날짜 추출 문법 밖이므로 날짜 선택 요청으로 차단한다. 자유문장 날짜 계산을 추가한 것이 아니다.

진단용 새 주장 검사기와 앱 사실 검사기는 서로 다르다. 앱에 `system_context_scoring_v1_1`을 연결하거나 P0를 바꾸지 않았다. 의도 수정은 일반적인 자연어 이해·모든 미래 표현의 정확한 분류를 보장하지 않는다.

## 실제 Claude Code 점검

읽기 전용 Claude Code CLI에 새 의도 정책·합성 테스트만 제공했다. 날씨 질문 오탐, `내일이` 조사 변형, 여러 주제 제외 뒤 모호한 요청의 반례를 직접 재현해 합성 회귀에 추가했다. 앞선 진단 검사기 검토와 별도로 수행했고 모델 원문·sealed blind·다른 세션 자료를 제공하지 않았다.

## CPU canary와 공개 산출물

- build: `build-641ac655f656`.
- [공개 집계](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v1.0.0/build-641ac655f656/aggregate.json), [manifest](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v1.0.0/build-641ac655f656/build_manifest.json), [검증](../../data/reports/saju_1b_baseline/dashboard-intent-canary/v1.0.0/build-641ac655f656/verification.json).
- 후보 회귀 25개 통과·실패/건너뜀 0. 같은 18개 의도 행렬은 허용 7·사전 차단 11로 HTTP·직접 생성·worker 실행 대상 확인 경로에 적용했다. 새 CPU 프로세스에서도 HTTP·직접 호출 행렬을 다시 통과했고 Torch/Transformers/PEFT를 로딩하지 않았다.
- 실제 loopback HTTP socket은 임시 포트만 사용하고 종료했다. 직접 생성 경로의 파일 저장·원응답 보존·의도/날짜 진단 metadata를 검증했다. 모델 대신 대체 생성기를 사용했으며 worker launcher는 v1.16 자기 파일·새 config를 가리킴을 확인했다.
- 실제 모델을 로딩하는 CLI worker 전체, GPU 생성, 운영 브라우저, 서비스 교체는 미실행이다. CPU 경로 통과를 운영 적용이나 모델 품질 개선으로 보고하지 않는다.
- 첫 18개 HTTP 행렬 실행은 기존 분당 10회 제한에 걸렸다. 테스트 서버 생성 전에 합성 예산을 분리해 수정했다. 후보 config의 실제 10회 제한은 유지하고 version 정규화 회귀로 확인했다.
- 공개 보고서의 발행·변조·건너뜀 거부·재검증 4개 회귀를 더해 CPU 표적 29개 통과. source 변경 중 발행과 기존 공개 파일 덮어쓰기를 거부한다.

```bash
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary plan
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary execute --execute
.venv-data/bin/python -B -m scripts.evaluation.dashboard_intent_canary verify --build build-641ac655f656
uvx ruff check scripts tests
.venv/bin/python -B -m unittest discover -s tests -q -b
git diff --check
```

발행·별도 verify·동일 실행 재사용이 같은 build/hash로 통과했다. 부모 source 460개·S2 공개/입력/응답·모델·학습·Phase 6·Runtime release는 그대로다. 운영 PID `3144071`·재시작 0 유지, GPU 계산 프로세스 없음까지 확인했다.

## 진행 기록

- 2026-09-15: 의도 후보·18사례 CPU 행렬·불변 공개 canary를 구현하고 개별 검증을 완료했다. 실행 정본·현재 기준선·50 단계와 최상단 포인터를 최신 상태로 맞춘다. 다음 별도 모델 실험은 S3이며 서비스 전환이나 추가 학습은 하지 않는다.
- 최종 전체 검증: `.venv/bin/python -B -m unittest discover -s tests -q -b` → **936개 통과, 실패·오류·건너뜀 0, 84.149초**. Ruff와 diff 검사도 통과했다. 이전 935개 실행에서 남은 문서 검사 2건은 과거 v1.15 후보/단계 문구를 요구하던 assertion을 현재 v1.16·CPU 파생 상태와 실제 공개 집계에 결합해 수정했다.
- 정본·링크·자동 정책 표적 21개 통과. 새 검사·canary의 공개 파일을 별도로 재검증했으며 private 원응답의 Git 추적 0·0700/0600 권한, 운영 코드 `0e77621846c4e9894cb40d801e84d59ad57cb0de`·8767 listener만 유지됨을 확인했다. 다른 작업 폴더·세션은 변경하지 않았다.
