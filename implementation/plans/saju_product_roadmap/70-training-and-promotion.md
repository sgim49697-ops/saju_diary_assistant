<!-- 70-training-and-promotion.md - v3.1 비학습 Gate 이후의 조건부 GPU 실행 경계를 기록한다. -->

# 70. 조건부 학습과 승격

이 단계는 00~60 구현 범위에서 실행하지 않는다.

1. 고정 K0에서 MIX2K-v3.1 Full FT diagnostic을 먼저 실행한다.
2. 자동 zero-tolerance·workflow·grounding·일반 회귀 Gate를 모두 통과해야 10K를 검토한다.
3. 10K와 20K는 각각 K0에서 독립 run으로 시작한다.
4. 데이터 규모와 train context 길이를 한 실행에서 동시에 바꾸지 않는다.
5. 새 final 후보가 동결된 뒤에만 새 version의 자동 sealed set을 단회 사용한다.
6. 기존 `spent_completed` split과 KI20 checkpoint를 열거나 이어학습하지 않는다.
7. 학습 성공은 Runtime release·운영 모델 교체를 자동 승인하지 않는다.

모든 GPU 실행은 별도 명시 승인, 유휴 GPU, 고정 cu130 환경, native JIT, private run root를 요구한다.
