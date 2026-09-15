<!-- 00-current-baseline.md - 원격·진단 후보·운영 상태와 완료 산출물의 권한을 구분한다. -->

# 00. 현재 기준선과 권한 경계

이 문서는 후속 로드맵의 **현재 상태 정본**이다. 2026-09-15 S0/S1·S2 및 CPU 재집계·v1.16 의도 후보 검증을 반영한다. PR #28 통합·기존 학습·응답·데이터 이력은 아래 연결된 기록에 보존한다. 실행 순서는 [로드맵 README](README.md), 진단 상세는 [50번 문서](50-automatic-model-evaluation.md)가 소유한다.

## Git와 실제 운영을 구분한 기준선

| 구분 | 확인 값 | 해석 |
|---|---|---|
| master 통합 기준 | `3bb0ce2affa50e395ef21b473f1e27c5ff5fdb38` | PR #28 병합 지점; 이후 상태 정리 커밋도 master에서 진행 |
| 진단 후보 근거 | `26462137f9a4ef34adb2d3db0dd6eaff6282b309` | v1.15·20문장 검증·병합 완료·운영 미배포 |
| 최신 전체 경로 진단 | 구현 `98f44d1`, `build-c39b4bce5089` | S2 342요청·재검증 완료, 검사기 오탐 별도 확인 |
| 새 검사 버전 파생 | 구현 `53c251a`, 결과 `46d5725`, `build-8547c487c858` | 같은 312응답 CPU 재채점·30차단 보존, 새 생성 없음 |
| 최신 앱 후보 | v1.16, CPU canary `build-641ac655f656` | 의도 정책 격리·25개 경로 회귀 통과, 운영 미배포 |
| 현재 작업 위치 | 원본 프로젝트 폴더의 `master` | `f34f856`·`56b0ecb`·작업 규칙 `f9173e5`까지 통합, 새 작업 브랜치 없음 |
| 운영 service | `saju-mix2k-r16-dashboard-v1-14.service`, dashboard v1.14 | active/running, `127.0.0.1:8767` |
| 운영 코드 | `0e77621846c4e9894cb40d801e84d59ad57cb0de` | 원격 master나 진단 후보 HEAD와 같다고 가정하지 않음 |
| 모델 선택 | 기본 `ki20_final`, R16 선택 가능 | 진단의 관찰은 기본 모델 교체 승인이 아님 |

작업 재개 때 Git·세션·서비스 상태를 다시 확인한다. 기존 service·세션·다른 작업 브랜치를 이 문서 갱신만으로 교체하거나 종료하지 않는다.

## 완료된 구현과 승인 범위

| 영역 | 확인된 상태 | 후속 작업의 처리 |
|---|---|---|
| KI10·KI20 | Full FT 완료, KI20 20K·1 epoch·2,500 step·final reload 완료 | checkpoint·학습 이력 불변 |
| K0 기반 LoRA | R8·R16·R32 각각 2,000행·1 epoch·250 step 완료 | 학습 완료와 5-arm 평가 완료를 구분 |
| Phase 6 | `eval-e8630962cab2`, `AUTOMATED_REPAIR_REQUIRED` | 단회 완료, 재실행·재판정 금지 |
| 과거 대화 진단 | 500건 및 2,048↔3,584 장문 진단 완료 | 공개 집계만 이력 근거로 사용 |
| 20문장 기준선 | 확정 20문장×3모델, 60요청·54생성·6차단 | 과거 개발 진단 이력 유지 |
| 최신 전체 경로 진단 | S2와 새 검사 버전 CPU 재집계 검증 완료 | S3 지시문이 다음 별도 과제, S3~S6 미실행 |
| 원국 | v1.4, 정규화 양력 1920-01-07~2026-08-31 | 승인 chart-only 부모 release |
| 단일 일진 | v1.5, 2026-09-02~2049-12-31 | KST 정오 기준 공식 날짜 label, conformance v10 8,522/8,522 |
| 기간 범위 | 일별 label release, conformance v11 263,717 window | 1~31일 범위 구현·검증 완료, 미래 분 단위 절입 승인 아님 |
| 단일 날짜 관계 | relation v1 release·전수 검사 완료 | 십신·직접 관계 존재만, 길흉·범위 관계 해석 승인 아님 |
| 앱 후보 | v1.12·v1.13·v1.15 부모 보존, v1.16 의도 분리 CPU canary 완료 | 기본 포트 8769·별도 세션·기능 기본 off, 현재 서비스는 v1.14 |
| strict/full | 미래 물리 절입·미승인 범위·대운 등 차단 | 기존 제한 release와 별개로 false 유지 |

기간 release는 `saju-period-daily-label-release-v1.0.0-59e326f8f086`, 관계 release는 `saju-natal-day-relation-release-v1.0.0-554bb9bfaea9`다. 계산 권위는 [Runtime 정본](../saju_runtime_calculator_adoption.md)과 각 불변 registry를 따른다.

## 데이터와 모델 진단 상태

- 현재 R16은 `v1.0.1/build-54836f556b4f`의 2,000행으로 학습했다. 세 rank의 완료·artifact 검증은 [LoRA 계획](../mix2k_v4_chart_day_lora.md)의 2026-09-05 기록을 따른다.
- 별도 v1.1 보정 `repair-23340fc31022`는 마지막 확인 checkpoint 기준 accepted 238/400, 판정 대기 3, 초안·재작성 대기 159다. 현재 R16에 반영되지 않았다. 자동 재개하지 않고 50-D에서 보정 범위의 적합성을 먼저 판단한다. 다른 세션의 실행을 취소하는 지시는 아니다.
- 기존 `MIX20K-v3.0.1-repaired/build-94eb7b543490`은 비학습 후보로 보존한다. 당시 chart 4,350회·period 900회, canonical 대기 3,800행·exact duplicate 참여 2,035행 등은 [보정 정본](../mix20k_v3_repair_plan.md)의 이력이며 현재 2K 학습 데이터 통계와 섞지 않는다.
- 과거 [20문장 공개 집계](../../../data/reports/saju_1b_baseline/dashboard-prompt20/v1.0.0/build-9ab2958c83dc/aggregate.json)는 연결 구조 검사 K0 8/13·R16 10/13·KI20 8/13이다. 분모는 생성 18건 중 연결 13건이며 정확도나 의미 품질 점수가 아니다.
- 최대 입력 2,024 token·제외 대화 0, 첫 질문 12개 그룹 token identity 일치가 확인됐다. 이번 실패를 context 용량 부족으로 단정할 근거는 없지만 컨텍스트 간섭까지 배제하는 결과는 아니다. 자동 검사기의 누락·오탐과 실제 모델 오류를 분리하며 [전체 경로 진단](../saju_system_context_diagnosis.md)에서 필요한 정보만 제공하는 조건과 모델 크기를 교차 비교한다.
- 최신 S2는 입력 최대 1,692 token·이력 삭제 0이다. 동결 자동 점수에서 R16 FULL→MIN 회귀 8개 중 6개가 검사기 오탐으로 확인됐으므로 실제 정확도 차이나 모델 우열로 사용하지 않는다. [완료 기록](../../history/2026-09-15-system-context-diagnosis.md)에 공개 집계·위치 대조·한계·다음 검사 보강을 연결한다.
- [새 파생 집계](../../history/2026-09-15-system-context-rescore.md)의 R16 필수 사실 FULL/MIN은 11/24·6/24 PASS, 판단 불가 7·8개다. 기존 오탐 6개는 PASS 2·판단 불가 4로 바뀌었고 실제 MIN 오류 2개는 FAIL을 유지한다. 앱 의도 개선과 새 모델 성능을 혼합하지 않는다.
- 실제 KI20 학습 길이 768, R16 2,048을 재확인했다. S2 48개 입력은 FULL/MIN 모두 44개가 768 token을 넘었다. 이 학습/serving 차이와 같은 1.3B 내부 비교만으로 크기 원인을 확정하지 않는다.

## 과거 audit의 적용 범위

`project-audit-v1.1.0`은 Phase 6 완료 상태, v3.0.1 manifest, v1.5 release·conformance v10, dashboard v1.11 fingerprint를 검사하는 **과거 범위의 audit**다. 아래 명령이 통과해도 v1.14 운영이나 v1.15 후보 전체 검증을 뜻하지 않는다. 과거 `project-audit-v1.0.0`도 그대로 보존한다.

```bash
.venv-data/bin/python -m scripts.status.project_audit \
  --config configs/data_versions/saju_1b_baseline/project-audit-v1.1.0.json \
  validate-contract
.venv-data/bin/python -m scripts.status.project_audit \
  --config configs/data_versions/saju_1b_baseline/project-audit-v1.1.0.json \
  verify
```

최신 후보 검증은 [v1.16 CPU 완료 기록](../../history/2026-09-15-dashboard-v116-intent.md)을 따른다. [v1.15](../../history/2026-09-05-dashboard-v115-grounding.md)·[20문장](../../history/2026-09-05-dashboard-prompt20.md)은 부모 이력으로 보존한다. private artifact가 없는 격리 환경에서 생기는 전체 회귀 오류를 성공으로 기록하지 않는다.

2026-09-14 병합 후 841건의 실패 5·오류 17은 2026-09-15 `455fd9e`에서 복구했다. 과거 승인 source pin은 유지하고 기능 fixture·시계 주입을 분리했으며 당시 전체 846건을 통과했다. 이어 [전체 경로 진단](../saju_system_context_diagnosis.md)의 S0/S1과 S2 runner를 구현·실행했다. S2 342요청·재구성 검증을 완료했고 실행 후 `.venv/bin/python -B -m unittest discover -s tests -q -b` 879건 전부 통과·건너뜀 0이다. 테스트 통과가 새로 확인한 검사 문법 결함의 부재를 뜻하지 않으며 세부 검증·한계는 연결된 계획의 진행 기록을 따른다.

## 변경하지 않는 권한

- Phase 6은 완료 상태를 유지하고, 소비된 sealed blind `spent_completed`는 열거나 재사용하지 않는다.
- 자동 계약 밖 자연스러움·의미 품질은 `not_measured`다. 계약 밖 평가를 완료 조건으로 추가하지 않는다.
- 새 데이터 생성·학습·Runtime release·모델 승격·서비스 전환은 문서 정합화나 진단 완료로 자동 승인되지 않는다.
- 원본·private 응답·출생정보·키·checkpoint·불변 report/config는 이번 변경 대상이 아니다.

## 진행 기록

### 2026-09-15 — CPU 재집계·v1.16 의도 후보 기준선 반영

- 새 검사 버전·파생 342요청 대조와 앱 CPU canary를 연결했다. 검사기 판단 불가와 실제 오류를 구분하며 모델 크기 원인은 계속 미확정이다. 다음 별도 작업은 S3다.
- v1.14 PID `3144071`·재시작 0·기존 코드 유지, GPU 계산 프로세스 없음. 전체 최종 회귀 결과·공개 hash·원문 보존은 [앱 완료 기록](../../history/2026-09-15-dashboard-v116-intent.md)과 실행 정본의 진행 기록에 고정한다.

### 2026-09-15 — 전체 경로·S2 실제 실행 상태 반영

- 312생성·30차단·재검증과 테스트 879건 통과를 반영했다. 검사기 오탐 때문에 자동 점수를 실제 정확도로 사용하지 않는 경계를 명시했다.
- 운영 v1.14 PID `3144071`·재시작 0·기존 코드 hash를 유지했다. [완료 기록](../../history/2026-09-15-system-context-diagnosis.md)의 공개 산출물만 연결하며 모델·학습·Phase·release 상태는 바꾸지 않았다.

### 2026-09-14 — PR #28 병합·원본 폴더 동기화

- `3bb0ce2`를 기준으로 최신 대화 후보·계획·분기 정책을 통합했다. 원본 폴더는 `master`로 전환했으며 운영 v1.14는 기존 코드·PID를 유지한다.
- 코드 반영과 배포를 구분하도록 현재 상태 포인터를 교정했다. 테스트·브랜치 정리와 자료 보존 결과는 [통합 기록](../../history/2026-09-14-default-branch-integration.md)을 따른다.

### 2026-09-14 — 운영·문서 부모 재확인

- 원격 master `b78f8e6`, 원인 분리 문서 부모 `f34f856`, 운영 v1.14 service active/running·재시작 0을 재확인했다. 진단 후보·운영 코드는 같지 않으며 기존 세션·service는 유지했다.
- 신규 응답·학습 결과로 오해하지 않도록 확인 시점을 분리했다. 문서 검증 결과와 후속 S0/S1 범위는 [새 계획의 진행 기록](../saju_system_context_diagnosis.md)을 따른다.

### 2026-09-02 — 과거 audit v1.1·현황 v1.4 완료 이력

- v1.5 release·v10 8,522일·v1.11 fingerprint를 검증하는 audit와 현황 `build-faf55ff6886d`를 발행했다. 당시 표적 검증·읽기 전용 private manifest quick audit을 통과했고 sealed payload·GPU·학습·승격은 실행하지 않았다.

### 2026-09-05 — 현재 상태와 이력 권한 분리

- 원격 master·진단 후보·v1.14 service의 서로 다른 commit을 기록하고 완료된 LoRA·기간·관계와 미완료 400건 보정을 분리했다.
- 현재 상태만 갱신했으며 검증 명령·결과는 [재정렬 기록](../../history/2026-09-05-model-cause-roadmap.md)을 따른다. 다음 실행은 50-A이고 과거 audit를 최신 앱 전체 Gate로 사용하지 않는다.
