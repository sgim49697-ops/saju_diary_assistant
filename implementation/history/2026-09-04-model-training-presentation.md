# 모델 학습 발표자료 제작 기록

## 진행 기록

### 2026-09-04 - 4페이지 발표용 PDF 제작

- 작업 요약: K0 모델 선정, LoRA 경량화, MIX2K 2,000행 데이터와 실제 학습 설정, Dashboard v1.14 실사용 입·출력을 16:9 발표자료 4페이지로 정리했다.
- 변경 범위: 재현 가능한 PDF 생성 스크립트, 발표자료 설명 문서, 최종 PDF를 추가했다. 모델·데이터·runtime 설정과 학습 산출물은 변경하지 않았다.
- 사실성: R16의 실제 학습 시간·파라미터·GPU memory와 실제 raw 출력 문장을 사용했다. teacher fallback provenance와 원국 네 기둥 누락 blocker를 함께 명시해 진단 모델을 production 결과로 오인하지 않도록 했다.
- 검증: ReportLab로 PDF를 재생성하고 PyMuPDF로 4페이지를 2배율 PNG로 렌더링해 잘림·대비·한글 표시를 전수 시각 검수했다. Ruff와 PDF page count·text extraction·metadata 검증을 수행한다.
- 남은 이슈: R16은 `production=false`이며, cross-provider teacher 계약과 고정 dev release blocker를 통과한 뒤에만 별도 승격할 수 있다.

