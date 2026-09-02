<!-- 00-current-baseline.md - 후속 구현이 상속할 현재 모델·데이터·Runtime·앱 기준선을 고정한다. -->

# 00. 현재 기준선과 권한 경계

## 구현 상태

| 영역 | 현재 상태 | 후속 작업의 처리 |
|---|---|---|
| KI20 | 20K Full FT·1 epoch·2,500 step·final reload 완료 | checkpoint 불변 |
| Phase 6 | `eval-e8630962cab2`, `AUTOMATED_REPAIR_REQUIRED` | 재실행·재판정 금지 |
| 대화 진단 | 500건과 2,048↔3,584 장문 진단 완료 | 자동 결과만 기준선 사용 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` | 학습 후보가 아닌 보정 입력 |
| 원국 | v1.4, 1920-01-07~2026-08-31 | 승인 부모 release |
| 단일 일진 | v1.5, 2026-09-02~2049-12-31 | 날짜별 label 부모 release |
| 앱 | dashboard v1.11 제한 운영 | 새 버전 canary 전까지 유지 |
| strict/full | 미래 물리 절입·주월년·관계 미승인 | false 유지 |

## 확인된 데이터 상태

- v3.0.1은 review·training projection 각각 20,000행이다.
- model-facing tool call은 chart 4,350회와 period 900회다.
- period 분포는 day 200, week 300, month 200, year 200이다.
- canonical 재계산 대기 `HARD_CANDIDATE`는 3,800행이다.
- exact duplicate 참여 행 2,035와 state·grounding·언어·정책·다양성 blocker가 남아 있다.
- 기존 runtime build script는 v1.2 release에 고정돼 있어 v1.5나 새 기간 release의 생성기로 사용할 수 없다.

## 정합화 Gate

새 `project-audit-v1.1.0`은 payload를 열지 않고 다음만 검증한다.

1. Phase 6 완료·sealed blind `spent_completed`.
2. MIX20K-v3.0.1 private/public manifest와 학습 미실행.
3. v1.5 release와 conformance v10 `build-46185262164f`.
4. dashboard v1.11 config·자산·Grounding Gate fingerprint.
5. strict/full false, v3.1·학습·승격 false.

기존 `project-audit-v1.0.0`은 v1.3 당시의 불변 이력으로 보존한다.

## 실행 순서

```bash
.venv-data/bin/python -m scripts.status.project_audit \
  --config configs/data_versions/saju_1b_baseline/project-audit-v1.1.0.json \
  validate-contract
.venv-data/bin/python -m scripts.status.project_audit \
  --config configs/data_versions/saju_1b_baseline/project-audit-v1.1.0.json \
  verify
```

이 Gate가 통과하기 전 10번 문서의 release 산출물을 만들지 않는다.

## 진행 기록

### 2026-09-02 — audit v1.1·현황 v1.4 구현

- `project-audit-v1.1.0`에 v1.5 계약·release·conformance v10과 dashboard v1.11 config·entrypoint·asset·Grounding Gate hash 검증을 추가했다.
- 현황 v1.4 `build-faf55ff6886d`와 registry 포인터를 생성했다. 제한 release·앱 연결은 통과, strict/full Runtime은 차단, MIX20K-v3.1·학습·모델 승격은 금지로 분리했다.
- sealed blind payload는 열지 않았고 GPU·학습·tracked audit write를 실행하지 않았다.
- 격리 worktree의 표적 자동 검증은 통과했다. 기존 Git 제외 private build가 있는 master에서 통합 quick audit을 한 번 더 실행한 뒤 00 Gate를 완료로 전환한다.
