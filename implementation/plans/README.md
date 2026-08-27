# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본

종합 가이드와 정본이 충돌하면 `saju_1b_10k_20k_baseline/README.md` 및 각 Phase 문서를 따른다. 데이터 감사용 HTML은 계획 문서에 복제하지 않고 다음 버전 경로에 둔다.

```text
data/reports/<dataset>/audit-review/<audit-version>/<audit-build-id>/reviewer-<reviewer-version>/
```

감사 입력이나 정책이 바뀌면 `audit-version` 또는 `audit-build-id`가 바뀌고, 화면 기능만 바뀌면 `reviewer-version`을 올린다. 기존 디렉터리는 덮어쓰지 않는다.

현재 사람 검수 대상은 다음 한 build로 고정한다.

```text
data/reports/saju_1b_baseline/audit-review/v1.1.0/build-e162d9b2b7dc/reviewer-v1.0.0/
```

HTML 파일을 직접 열면 원문 API에 연결되지 않는다. 저장소 루트에서 다음 명령을 실행하면 loopback 주소로 검수기가 열리며, 핵심 150건과 참조 151건을 같은 화면에서 전환할 수 있다.

```bash
.venv-data/bin/python scripts/data/phase2_review_web.py \
  --audit-version v1.1.0 \
  --build build-e162d9b2b7dc \
  --port 8765
```

## 팀원용 핵심 검수 공유본

동일 AI Hub 승인 범위의 팀원에게는 저장소 밖에 생성한 다음 일반 ZIP을 전달한다.

```text
/home/user/projects/saju-review-share-v1.1.0-build-e162d9b2b7dc-core150.zip
/home/user/projects/saju-review-share-v1.1.0-build-e162d9b2b7dc-core150.zip.sha256
```

이 ZIP은 핵심 검수 150단위·180레코드만 최소 투영해 담는다. 원천 locator·원천 ID·생년 좌표·비공개 메모·본 판정 ledger는 포함하지 않는다. 핵심 큐의 AI Hub 70, Nemotron 40, `bazi-sft` 20, YEJI 20단위는 위험 기반 검수 표본이며 학습 혼합비를 나타내지 않는다. 참조 151단위는 팀원 ZIP에 넣지 않고 원 담당자의 loopback 검수기에서만 확인한다.

팀원은 ZIP을 승인된 로컬 폴더에 모두 푼 뒤 `TEAM_REVIEW_GUIDE.md`, `DATA_USAGE_NOTICE.md`를 읽고 `START_HERE.html`을 연다. 검수자 표기를 입력하고 각 항목의 제안 판정·사유·메모를 저장하며, 중간에는 checkpoint JSON을 내려받는다. 완료 후 최종 JSON과 CSV만 반환한다. 반환 결과는 advisory 의견일 뿐 자동으로 본 판정에 합치지 않으며 원 담당자가 항목별로 재확인한다.

일반 ZIP은 암호화되지 않았다. 승인된 내부 전송 수단만 사용하고 Git·공개 링크·공용 드라이브에 올리지 않으며, 재전달하지 않고 검수 종료 뒤 ZIP과 압축 해제본을 삭제한다. 무결성은 ZIP과 같은 디렉터리에서 다음처럼 확인한다.

```bash
sha256sum -c saju-review-share-v1.1.0-build-e162d9b2b7dc-core150.zip.sha256
```

팀원이 반환한 JSON은 본 판정에 참고하기 전에 저장소 루트에서 다음처럼 검증한다.

```bash
.venv-data/bin/python scripts/data/phase2_export_team_review.py verify-feedback \
  --archive ../saju-review-share-v1.1.0-build-e162d9b2b7dc-core150.zip \
  --feedback /승인된/내부/경로/team-review-build-e162d9b2b7dc-final.json
```
