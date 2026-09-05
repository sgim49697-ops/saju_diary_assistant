<!-- 2026-09-05-model-cause-roadmap.md - 원인 분리 우선 계획 재정렬의 범위·집계 근거·검증을 기록한다. -->

# 원인 분리 우선 프로젝트 계획 재정렬

## 작업 범위와 결정

- 기준은 완료 진단 commit `26462137f9a4ef34adb2d3db0dd6eaff6282b309`다. `codex/model-cause-roadmap` 격리 worktree에서 문서와 문서 정합성 테스트만 변경했다.
- [로드맵 README](../plans/saju_product_roadmap/README.md)는 실행 순서, [00 기준선](../plans/saju_product_roadmap/00-current-baseline.md)은 현재 상태, [50 진단](../plans/saju_product_roadmap/50-automatic-model-evaluation.md)은 원인 분리 상세를 소유한다. 최상단 세 계획은 제품·Runtime·모델 요약과 링크로 통합했다.
- 다음 순서는 50-A 오류/검사기 분리 → 50-B R16 지시문 후보 하나 비교 → 50-C K0와 큰 동일 계열 Instruct 기본 모델 비교 → 50-D 데이터/입력 점검이다. 큰 기본 모델 비교는 필수이며 모델 등록·실행 가능성 검증은 후속이다.
- 60/70은 조건부다. 진단 완료를 모든 응답의 정답·품질 승인과 구분하고 고정 Full FT 우선·자동 400건 재개/재학습 지시를 제거했다. 비자동 평가 절차·필수 표본·승격 요구도 활성 요약 문서에서 제거했다.
- 10~40의 완료 구현·산출물, Phase 0~6 상태, 불변 report/config, 학습 build와 checkpoint는 변경하지 않았다. 30/40 문서에는 당시 v1.11 운영과 현재 상태를 구분하는 연결만 추가했다. 이번에 모델 다운로드·GPU 생성·teacher 호출·데이터 생성·학습·서비스 전환·병합은 하지 않았다. 기존 sealed blind와 과거 비공개 평가 원출력도 열지 않았다.

## 현재 상태 확인

- 원격 master 조회 값은 `b78f8e630261db7a1561c649d5fadac91e321d58`, 진단 후보는 `2646213`으로 서로 다르다. 새 문서 브랜치는 후보에 의존하며 master에 병합됐다고 기록하지 않는다.
- 기존 `saju-mix2k-r16-dashboard-v1-14.service`는 active/running, 운영 코드 `0e77621846c4e9894cb40d801e84d59ad57cb0de`다. v1.15는 미병합·미배포 후보로 유지한다.
- 공유 작업트리의 기존 브랜치·index, 최상단 질문 문서와 외부 ZIP, 다른 세션 변경을 건드리지 않는다. 확정 20문장은 별도 policy 정리 대상으로 삼지 않는다.
- 기존 [20문장 공개 검증](../../data/reports/saju_1b_baseline/dashboard-prompt20/v1.0.0/build-9ab2958c83dc/verification.json)은 808건 전체 테스트에서 실패 4·오류 37·skip 37을 기록한다. 이를 비교 기준으로 사용하며 전체 green으로 해석하지 않는다.

## 학습 데이터 집계 근거

현재 R16 학습 입력은 `mix2k-v4-chart-day-8k/final/v1.0.1/build-54836f556b4f`의 `train_2000.jsonl`이다. SHA-256은 `a58904fa4968501f36be2e03e79d783ca6d4126164bd62fb859a924583e2293c`다. 고정 합성 데이터의 JSON 구조와 집계만 읽었으며 원문·개별 ID는 공개하지 않는다. 이번 변경에서는 이 데이터를 수정하거나 학습에 사용하지 않았다.

| task axis | 행 수 |
|---|---:|
| structured_fact_schema_literacy | 300 |
| chart_facts_natural_explanation | 300 |
| chart_day_today_flow | 450 |
| followup_explain_grounding | 300 |
| intake_state_correction | 250 |
| general_korean_empathy | 250 |
| uncertainty_blocked_boundary | 100 |
| hard_fact_short_qa | 50 |

- 합계 2,000행을 SHA와 함께 재확인했다. 일반 공감 250행의 원국 미연결 조건은 고정 [데이터 계약](../../configs/data_versions/saju_1b_baseline/mix2k-v4-chart-day-8k-v1.0.1.json)의 `runtime_scope=none`과 일치한다.
- assistant 메시지 중 비어 있지 않은 줄이 3개 이상인 메시지를 포함한 행은 1,751개다. 줄 수는 문장 수와 같지 않으며 형식 편중 가능성을 점검하는 단서다.
- user 메시지에서 정규식 `(?:두|2)\s*문장`과 `사주.{0,15}(?:말고|빼고|없이|아니라)`에 일치하는 행은 각각 0건이다. 다른 표현·맥락의 동등 예제가 없다는 뜻은 아니고 원인 확정도 아니다.
- 위 조건으로 행 단위 중복 없이 집계했다. 기존 최소 3문장/3줄 계약·JSON·build fingerprint를 소급 수정하지 않았다. 새 보정 범위는 50-D에서 입력 전달과 분리해 판단한다.

## 검증 명령과 결과

공유 프로젝트의 `.venv-data/bin/python` 절대 경로를 격리 worktree에서 사용한다. 문서 정책은 활성 plans/contracts/docs와 최상단 요약 세 파일을 검사하고 사용자 합성 질문 문서는 포함하지 않는다.

```bash
uvx ruff check scripts tests
/home/user/projects/saju_diary_assistant/.venv-data/bin/python -m unittest \
  tests.test_saju_product_roadmap \
  tests.test_phase6_technical.Phase6TechnicalTests.test_canonical_docs_forbid_person_dependent_gates -v
/home/user/projects/saju_diary_assistant/.venv-data/bin/python -m unittest discover -s tests -q
git diff --check
```

- 전체 `scripts/tests` Ruff와 `git diff --check`를 통과했다.
- 수정 문서 16개의 로컬 Markdown 링크 139개와 문서에 적힌 실행 모듈 경로 5개가 존재함을 확인했다. 실제 모델/데이터 실행 명령을 재실행한 검증은 아니다.
- 문서 정합성·정본 정책 12건을 통과했다. 상위 요약 세 파일도 비자동 Gate 금칙 검사에 포함하고, 확정 질문 문서 SHA가 그대로임을 확인했다. 현재 상태/정본 소유권·50-A~D 순서·큰 기본 모델 필수 비교 조건·60/70 보류·공개 집계 일치·연결 경로 존재를 검사했다.
- 기본 `umask 022`의 전체 회귀는 **815건, 실패 4·오류 37·skip 37**이다. 이전 808건의 실패/오류 항목 41개와 이름·subtest·순서까지 일치했고 추가/해소 항목은 0개다. 신규 정합성 테스트 7건은 모두 통과했다. 전체 green으로 기록하지 않는다.
- 기존 문제는 격리 worktree에 없는 고정 모델/tokenizer·source manifest·private artifact, 데이터용 환경에 없는 `torch`, 기존 계약/fixture 선행 조건 등의 범주다. 문서 수정으로 이 조건을 우회하거나 승인된 artifact를 복제하지 않았다. 이 결과는 전체 저장소가 환경 독립적으로 통과한다는 보장이 아니다.
- 변경 경로 검사에서 `configs`, `data`, `scripts`, 확정 질문 문서와 생성된 현황판의 diff는 0이다. 원시 출력·모델·키를 추가하지 않았고 공유 작업트리·index도 기존 상태 그대로다.
- 기존 v1.14 service는 동일 PID 3144071로 active/running을 유지했다. 모델 실행·학습·서비스 교체·병합은 이번 검증에 포함하지 않았다.

## 남은 작업

다음 구현은 50-A의 오류 분리·합성 최소 회귀·새 진단 계약과 dry-run이다. 50-B/C 실제 모델 실행과 50-D 후속 판단은 아직 하지 않았다. 진단이 끝나기 전에 데이터 확대·학습·기본 모델 변경을 자동 시작하지 않는다.
