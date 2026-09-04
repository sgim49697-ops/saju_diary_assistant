# K0·MIX2K·LoRA 모델 학습 발표자료

[발표용 PDF](./saju-model-training-overview-4p.pdf)는 16:9 화면 비율의 밝은 테마 4페이지 자료다. 첫 페이지 왼쪽 상단에는 발표자 표기 `특화형_4_멘티_김슬기_김민희`를 14pt로 표시한다. 저장소 `master` 커밋 `85e0171`에서 확정된 모델·데이터·학습·Dashboard v1.14 사실만 사용했다.

## 페이지 구성

1. K0-INSTRUCT 선정 이유와 KI20·Full FT 대비
2. LoRA r=8/16/32 경량화 구조와 R16 실측 자원
3. MIX2K 2,000행 구성, token audit, 실제 학습 recipe
4. production-like runtime 입력과 R16 실제 raw 출력

## 주요 근거

- 모델: `kakaocorp/kanana-2-1.3b-instruct` revision `bf4786aa2a1908adce942d53976270132732f720`, 1,291,478,272 parameters
- 데이터: `build-54836f556b4f`, 2,000행, full runtime snapshot, `max_length=2048`
- 학습: LoRA r=16, 18,677,760 trainable parameters, 1 epoch, 250 optimizer steps, LR `5e-5`
- 실측: 2,098.756초, training loss `1.1570811777114869`, peak reserved 4.34 GiB
- 실사용: 778 input tokens, 16.7초, peak allocated 2.69 GiB
- 상태: R16은 원국 네 기둥 전체 누락 blocker와 teacher provenance 제한 때문에 `production=false`인 진단 모델

근거 파일은 다음과 같다.

- `configs/model_versions/saju_1b_baseline/mix2k-v4-lora-v1.0.1.json`
- `configs/data_versions/saju_1b_baseline/mix2k-v4-chart-day-8k-v1.0.1.json`
- `configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.14.0-r16-diagnostic.json`
- `implementation/plans/mix2k_v4_chart_day_lora.md`
- local run artifact `train-f340a82c76d3/training_manifest.json`

## 다시 생성하기

프로젝트 의존성을 변경하지 않고 임시 ReportLab 환경에서 생성한다.

```bash
uv run --with reportlab scripts/presentations/build_model_training_deck.py
```

스크립트는 Windows의 맑은 고딕을 우선 사용하고, 없는 환경에서는 WenQuanYi Zen Hei로 대체한다.
