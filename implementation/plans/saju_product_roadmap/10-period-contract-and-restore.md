<!-- 10-period-contract-and-restore.md - 기간 요청 v2와 process 독립 원국 재검산 계약을 정의한다. -->

# 10. 기간 계약과 원국 복원

## 목표

기존 v1.5 단일 일진을 부모로 삼되 모델이 opaque ID를 만들거나 현재 process cache에 의존하지 않는 기간 실행 경계를 만든다.

## 공개 요청 계약

```json
{
  "type": "request_period",
  "request": {
    "schema_version": "saju-period-request-v2",
    "date_expression": "today|tomorrow|this_weekend|this_week|this_month|explicit",
    "start_date": null,
    "end_date": null
  }
}
```

- `explicit`일 때만 두 ISO 날짜를 사용한다.
- 모델·브라우저는 `chart_id`, timezone, reference clock, policy ID, release ID, session revision을 만들지 않는다.
- executor가 암호화 session과 서버 KST clock에서 내부 envelope를 주입한다.
- 자유문 날짜 parser를 추가하지 않는다.

## 복원 계약

1. 암호화 session의 birth slot을 현재 v1.5 입력으로 재구성한다.
2. 같은 release·profile·source·HMAC signer로 exact 원국을 재계산한다.
3. 저장된 chart ID, 입력 fingerprint, 공개 hard facts hash가 모두 같아야 한다.
4. 일치한 경우에만 새 기간 engine에 내부 chart authorization을 전달한다.
5. 정정·변조·release 차이·range/unknown 원국은 차단한다.

## 구현 단위

- `configs/runtime/period/`: request/output/authority/release schema와 hash registry.
- `scripts/runtime/period_v1/`: strict loader, 상대 날짜 resolver, chart rehydrator, CLI.
- `tests/test_period_contract_v1.py`: 중복 JSON key, enum, 교차 field, host timezone, 변조, process restart.

## 완료 Gate

- process 재시작 뒤 exact chart 복원 100%.
- model-generated opaque ID 0.
- stale revision·변조·range/unknown silent fallback 0.
- 공개 응답의 출생값·ID·key·private path 0.
