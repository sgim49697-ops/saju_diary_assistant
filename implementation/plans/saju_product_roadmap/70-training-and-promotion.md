<!-- 70-training-and-promotion.md - 원인 분리 이후 모델·학습 방식·규모와 운영 승격을 각각 판단한다. -->

# 70. 조건부 학습과 승격

## 상태: 조건부 보류

실행 순서는 [로드맵 README](README.md)를 따른다. **50 완료나 60 build 통과만으로 학습은 자동 진행하지 않는다.** 이번 범위는 문서·정합성 테스트이며 GPU·학습·모델 승격을 실행하지 않는다.

## 완료 이력과 새 실험의 분리

- KI10·KI20 Full FT와 K0 기반 R8·R16·R32 LoRA는 완료 이력이다. [LoRA 계획](../mix2k_v4_chart_day_lora.md)의 고정 build·adapter·계약을 보존하고 다시 학습하지 않는다.
- [50-C](50-automatic-model-evaluation.md)의 K0 기본 모델↔큰 동일 계열 Instruct 기본 모델 비교는 학습 없는 원인 분리다. 그 비교를 R16 학습 효과나 운영 모델 승격으로 해석하지 않는다.
- 후속 학습의 모델 크기·LoRA/Full FT 방식·데이터 규모·context 길이는 원인 분리 결과와 자원 검증을 근거로 **별도 결정**한다. Full FT 우선이나 2K→10K→20K 자동 확대 순서를 고정하지 않는다.

## 학습 검토를 다시 열 조건

1. 50-A~D 결과에서 해결할 오류와 재검증 조건이 특정돼야 한다. 표적 데이터가 필요하면 [60번 문서](60-mix20k-v3-1-build.md)의 새 비학습 Gate를 먼저 거친다.
2. 모델 revision·학습 방식·데이터와 평가 split·tokenizer/template·decoding·context·seed·예산·중단/재개 조건을 새 versioned 계약으로 고정한다. 데이터 규모·모델 크기·context를 동시에 바꿔 원인을 다시 섞지 않는다.
3. 명시 실행 승인 뒤 유휴 GPU·여유 VRAM·고정 환경·일치하는 Python 개발 헤더·native JIT·adapter/base 무결성·private run root를 검증한다. 승인된 현재 환경을 임의 교체하지 않는다.
4. 기존 KI20을 이어학습하지 않는다. 새 실험은 선택한 기본 모델의 고정 revision에서 별도로 시작하며 비교 baseline과 이전 checkpoint를 보존한다.
5. 새 final 후보가 동결되고 최종 평가가 별도 승인된 뒤에만 새 version의 자동 sealed set을 단회 사용한다. 이미 소비된 `spent_completed` split은 열거나 재사용하지 않는다.
6. 학습 완료·자동 진단 완료·모델 승격·Runtime release·운영 서비스 전환을 별개로 기록한다. 측정하지 않은 자연스러움·의미 품질은 `not_measured`로 남기고 계약 밖 평가를 완료 조건으로 추가하지 않는다.

## 진행 기록

### 2026-09-05 — 학습 방식과 규모를 사후 결정으로 정정

- MIX2K-v3.1 Full FT를 먼저 실행하던 지시를 제거하고 완료된 LoRA 실험과 새 학습 검토를 분리했다.
- 검증 명령·결과는 [재정렬 기록](../../history/2026-09-05-model-cause-roadmap.md)을 따른다. 추가 학습·새 봉인·release·서비스 전환은 미실행이다.
