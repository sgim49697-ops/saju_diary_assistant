# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본
- `mix20k_v3_repair_plan.md`: 외부 MIX20K-v3 후보의 감사·보정·검수·학습 차단 정본
- `saju_runtime_calculator_adoption.md`: 한국 만세력 계산 core·공식 conformance·v3.1 이관을 결정하는 독립 정본

종합 가이드와 정본이 충돌하면 해당 workstream 정본을 따른다. 학습 Phase·현재 모델 상태는 `saju_1b_10k_20k_baseline/README.md`, v3 후보는 `mix20k_v3_repair_plan.md`, 계산기·공식 근거·release 경계는 `saju_runtime_calculator_adoption.md`가 우선한다.

## 현재 정본 상태

| workstream | 현재 상태 | 승인 경계 |
|---|---|---|
| 10K/20K baseline | Phase 0~5 완료, KI20 `run-1f5d732cae67` 학습·재로딩 완료 | Phase 6 미시작, sealed blind 미열람, production 금지 |
| MIX20K-v3 | `v3.0.1-repaired/build-94eb7b543490` 기술 후보·비학습 preflight 완료 | canonical 3,800행·검수·다양성·state/grounding·serving blocker, v3.1·학습 금지 |
| 만세력 runtime | Skyfield v1.3 candidate와 conformance v8 `build-8bd88d6db03a` 통과 | strict Gate·release·앱 연결 차단, 결과는 `HARD_CANDIDATE` |

루트 [`PROJECT_STATUS.html`](../../PROJECT_STATUS.html)은 위 세 workstream의 공개 가능한 현재 집계를 보여준다. 과거 진행 기록과 versioned config/report는 당시 판단의 불변 이력이며 현재 포인터로 읽지 않는다.

데이터 감사용 HTML은 계획 문서에 복제하지 않고 다음 버전 경로에 둔다.

```text
data/reports/<dataset>/audit-review/<audit-version>/<audit-build-id>/reviewer-<reviewer-version>/
```

감사 입력이나 정책이 바뀌면 `audit-version` 또는 `audit-build-id`가 바뀌고, 화면 기능만 바뀌면 `reviewer-version`을 올린다. 기존 디렉터리는 덮어쓰지 않는다.

현재 승인된 원천 감사와 24K 전처리 검수 대상은 다음 build로 고정한다.

```text
data/reports/saju_1b_baseline/audit-review/v1.2.0/build-ca756f3eb89f/reviewer-v1.1.0/
data/reports/saju_1b_baseline/preprocessing-staging/v0.1.0/build-109815ee6879/reviewer-v1.0.0/
```

Phase 0~2 전체 재검수 요약과 기계 판독 결과는 다음 버전 경로에 있다. 둘 다 원문 sample을 포함하지 않는다.

```text
data/reports/saju_1b_baseline/phase-verification/v1.0.0/review-20260828/index.html
data/reports/saju_1b_baseline/phase-verification/v1.0.0/review-20260828/verification_report.json
```

HTML 파일을 직접 열면 Git 제외 staging API에 연결되지 않는다. 저장소 루트에서 다음 명령을 실행하면 `127.0.0.1` loopback 주소로 검수기가 열리며 BaZi 150건과 YEJI 150건을 전환할 수 있다.

```bash
.venv-data/bin/python scripts/data/phase2b_review_web.py \
  --build build-109815ee6879 \
  --port 8765
```

현재 주소는 `http://127.0.0.1:8765/`이다. 300건은 2026-08-27 사용자 지시에 따라 모두 일괄 수용 상태지만 항목별 명리 전문 검수가 수행된 것은 아니다. 공개 `APPROVAL.json`에는 `domain_item_review_performed=false`, `quality_certification_claimed=false`로 기록돼 있다.

## 팀원용 핵심·참고 검수 공유본

AI Hub #86의 동일 신청에 포함됐거나 AI Hub로부터 별도 열람 권한을 명시적으로 확인받은 팀원에게만 저장소 밖에 생성한 다음 일반 ZIP을 전달한다. 단순히 같은 팀·회사에 속한 것은 열람 권한이 아니다.

```text
/home/user/projects/saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip
/home/user/projects/saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip.sha256
```

이 ZIP은 핵심 150단위와 참고 150단위, 총 340레코드를 최소 투영해 담는다. 원천 locator·원천 ID·생년 좌표·비공개 메모·본 판정 ledger는 포함하지 않는다. 핵심 큐의 AI Hub 70, Nemotron 40, `bazi-sft` 20, YEJI 20단위는 위험 기반 검수 표본이며 학습 혼합비를 나타내지 않는다.

팀원은 ZIP을 승인된 로컬 폴더에 모두 푼 뒤 `TEAM_REVIEW_GUIDE.md`, `DATA_USAGE_NOTICE.md`를 읽고 `START_HERE.html`을 연다. 검수자 표기를 입력하고 각 항목의 제안 판정·사유·메모를 저장하며, 중간에는 checkpoint JSON을 내려받는다. 완료 후 최종 JSON과 CSV만 반환한다. 반환 결과는 advisory 의견일 뿐 자동으로 본 판정에 합치지 않으며 원 담당자가 항목별로 재확인한다.

일반 ZIP은 암호화되지 않았다. 승인된 내부 전송 수단만 사용하고 Git·공개 링크·공용 드라이브에 올리지 않으며, 재전달하지 않고 검수 종료 뒤 ZIP과 압축 해제본을 삭제한다. 무결성은 ZIP과 같은 디렉터리에서 다음처럼 확인한다.

```bash
sha256sum -c saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip.sha256
```

팀원이 반환한 JSON은 본 판정에 참고하기 전에 저장소 루트에서 다음처럼 검증한다.

```bash
.venv-data/bin/python scripts/data/phase2_export_team_review.py verify-feedback \
  --archive ../saju-review-share-v1.2.0-build-ca756f3eb89f-core150-ref150.zip \
  --feedback /승인된/내부/경로/team-review-build-ca756f3eb89f-final.json
```
