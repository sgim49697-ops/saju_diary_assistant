# Phase 6 자동 기술평가 결정

- 평가 build: `eval-e8630962cab2`
- 상태: `completed`
- baseline 결정: `AUTOMATED_REPAIR_REQUIRED`
- 결정 입력: 저장소 내부 자동 기술 지표만 사용
- 의미 품질: `not_measured`이며 성능 주장에 사용하지 않음
- release·앱 연결·MIX20K-v3.1 생성·추가 학습: 모두 미승인

## 모델별 자동 Gate

- `K0-INSTRUCT`: `failed` · 실패 deterministic_contract, foreign_sentence, generation_clean, nonsealed_handoff, rule_contract, zero_tolerance
- `KI10-MIX-v2`: `failed` · 실패 deterministic_contract, nonsealed_handoff, rule_contract, zero_tolerance
- `KI20-MIX-v2`: `failed` · 실패 deterministic_contract, nonsealed_handoff, rule_contract, zero_tolerance

## 경계

이 결정은 baseline 기술 비교를 닫는 결과다. production 적격성, 사주 해석의 의미 정확성, runtime release 승인을 뜻하지 않는다.
