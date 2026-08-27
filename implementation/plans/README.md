# 계획 문서 안내

이 디렉터리는 사주 일기 도우미의 종합 조사 자료와 실행 정본을 함께 보관한다.

- `kanana_saju_dataset_guide.html`: 모델·데이터셋 조사 내용을 한 화면에서 확인하는 종합 참고 자료
- `saju_1b_10k_20k_baseline/README.md`: 실제 구현 순서, 버전, Gate를 결정하는 정본

종합 가이드와 정본이 충돌하면 `saju_1b_10k_20k_baseline/README.md` 및 각 Phase 문서를 따른다. 데이터 감사용 HTML은 계획 문서에 복제하지 않고 다음 버전 경로에 둔다.

```text
data/reports/<dataset>/audit-review/<audit-version>/<audit-build-id>/reviewer-<reviewer-version>/
```

감사 입력이나 정책이 바뀌면 `audit-version` 또는 `audit-build-id`가 바뀌고, 화면 기능만 바뀌면 `reviewer-version`을 올린다. 기존 디렉터리는 덮어쓰지 않는다.
