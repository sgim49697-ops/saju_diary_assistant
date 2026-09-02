<!-- 20-daily-range-runtime.md - 공식 대조된 날짜별 간지만으로 1~31일 기간 release를 정의한다. -->

# 20. 일별 label 범위 Runtime

## 승인 범위

- timezone은 `Asia/Seoul`만 허용한다.
- 시작일은 서버 KST 오늘과 v1.5 하한 중 늦은 날이다.
- 종료일은 `2049-12-31`, 한 요청은 최대 31일이다.
- `today`, `tomorrow`, 현재/다가오는 주말, 오늘부터 이번 주말, 오늘부터 월말, 명시 범위를 지원한다.
- 토요일의 `this_weekend`는 토~일, 일요일은 일요일 한 날이다.
- 어제·과거·연간 범위는 차단한다.

## 결과 계약

```json
{
  "status": "ok",
  "fact_authority": "HARD_GT",
  "period_scope": {},
  "days": [
    {
      "date": "2026-09-02",
      "year_ganzhi": "...",
      "month_ganzhi": "...",
      "day_ganzhi": "...",
      "authority": "SOURCE_HARD_FACT"
    }
  ],
  "boundary_capability": {
    "intraday_segments_supported": false,
    "future_physical_instant_claimed": false
  }
}
```

분 단위 `segments[]`, 대운, 사건 예측, 관계 해석을 넣지 않는다. 내부 `period_id`는 build/release domain HMAC으로 만들고 공개·모델 context에서 제거한다.

## Conformance v11

- v10 공식 matrix 8,522일을 전량 재검증한다.
- 길이 1~31일의 연속 window 263,717개를 전수 구성한다.
- 날짜 순서·중복·누락·label·authority mismatch 0이어야 한다.
- 월말·연말·윤일·토요일·일요일·동적 today floor fixture를 별도로 검증한다.
- 공식 snapshot에 provider 생성값을 쓰지 않는다.

통과 보고서와 결합된 `saju-period-daily-label-release-v1.0.0-*`만 다음 단계가 소비한다. 이 release는 strict/full Runtime 승인이 아니다.
