<!-- 60-mix20k-v3-1-build.md - 승인된 chart·기간·관계 release로 MIX20K-v3.1을 비학습 생성하는 계약이다. -->

# 60. MIX20K-v3.1 build와 비학습 preflight

## 입력 재작성

- 기존 v1.2 전용 generator는 수정하지 않고 v2 builder를 추가한다.
- chart 4,350회와 period 900회를 승인 release로 전수 재실행한다.
- period year 200건은 지원되는 day/week/month/최대 31일 explicit 사례로 교체한다.
- 해외 180건은 KR 사례로 교체하고 20건은 `UNSUPPORTED_REGION` trajectory로 유지한다.
- model argument에서 chart ID·timezone·policy·revision을 제거하고 executor envelope로 이동한다.
- Git 제외 0600 build 전용 HMAC key를 사용하며 production key를 데이터 생성에 사용하지 않는다.

## 자동 Gate

- canonical candidate 3,800행 mismatch 0.
- tool result provisional 0, unsupported relation 0.
- state·grounding·언어·정책 오류 0.
- tokenizer 768 초과, zero mask, EOS, serialization, parser round-trip 오류 0.
- 비봉인 prompt overlap과 소비된 sealed content hash overlap 0.
- exact duplicate 참여 ≤200, template/source/token 비중 계약 통과.

새 fingerprint 경로만 만들고 `training_promotion_allowed=false`로 종료한다. 원문·제한 데이터·내부 ID는 공개 보고서에 넣지 않는다.
