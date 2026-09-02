<!-- known_limitations.md - 후보 만세력 runtime의 지원 한계와 사용자-facing 차단 조건을 기록한다. -->

# Runtime 알려진 한계

- 대한민국 외 출생과 `Asia/Seoul` 외 timezone은 지원하지 않는다.
- 후보 계산은 1900~2049 밖을 지원하지 않는다. v1.5도 원국은 부모 v1.4와 같은 정규화 양력 `1920-01-07~2026-08-31`만 승인하고, 단일 일진은 서버 KST 오늘과 release 하한 `2026-09-02` 중 늦은 날부터 `2049-12-31`까지만 허용한다.
- 자정을 넘는 단일 시간 범위는 지원하지 않는다.
- 생시 미상·범위 입력은 단일 chart로 확정하지 않는다.
- DST gap은 차단하고 fold는 후보를 유지한다.
- `saju-runtime-python-v1.3.0` candidate는 불변 이력으로 남고 결과는 `HARD_CANDIDATE`다. `saju-runtime-python-v1.4.0`은 과거 공식 범위의 원국만 승인한 부모 release이며, `saju-runtime-python-v1.5.0`은 exact 원국에 결합된 단일 일진만 추가한다. feature 기본 off·production key 필수는 같다.
- v1.4 exact는 `HARD_GT`, range·unknown은 ±1초 양끝에서 공통 사실이 유지될 때만 `POLICY_BOUND_RULE`다. minute 격자와 공식 root ±1초가 겹치는 50개 분은 exact와 불안정 range에서 차단한다.
- KASI 자격 증명은 저장소 밖 0600 runtime 파일로만 사용했다. 음양력 54,787일 수집은 완료했지만 key 값이나 hash는 기록하지 않았다.
- KASI 24절기 OpenAPI는 1900~2049년 전수 scan에서 2000~2028년 696건만 반환했다. 별도 공식 현재 계산 다운로드는 1920~2100년 4,343/4,344행을 제공하며 유일한 누락은 비절입인 2030년 우수다. 절입은 2,172/2,172행이지만, 두 원천의 2011년 대한 1건은 날짜·분이 충돌한다.
- 1900~1919년 절입 240행은 공식 coverage가 없다. provider 생성값을 공식 snapshot에 채우지 않고 `PROFILE_DETERMINISTIC`으로 표시한다.
- 1964년 백로의 공식 현재 계산 `9월 7일 23:59`와 과거 디지털 역서 `9월 7일 24:00`은 서로 다른 vintage의 문서 사실이다. 후자는 다음 날 `00:00`으로 정규화되며, 어느 하나로 다른 원천을 덮어쓰거나 sub-minute 물리 정확도를 주장하지 않는다.
- KASI 공식 현재 계산은 최근접 분 표시와 과거 일반 오차 1초 이내를 설명하지만, 공개 행은 초 단위 oracle이 아니다. 미래 계산은 지구 자전 불규칙성 때문에 수초~수분 달라질 수 있다.
- Skyfield 1.55·고정 DE440s·내장 UT1은 공식 1,560행에서 날짜 mismatch 0, 원시 분 라벨 mismatch 22다. 과거 1,280행의 14건은 선언된 1초 불확실성 안이지만 미래 280행의 8건은 물리 순간 미판정 진단이므로 strict runtime provider Gate는 실패한다.
- Astronomy Engine은 공식 1,560행에서 분 mismatch 303, 날짜 mismatch 1이어서 우선 후보가 아니다. provider 교차 비교와 과거 v1.2 회귀 경로는 불변으로 보존한다.
- Astronomy Engine과 Skyfield/JPL DE440s의 1,800건 차이는 최대 80.666231초다. 동일 TT root 비교에도 평균 절대 11.513609초가 남지만 Skyfield root의 32회↔48회 차이는 최대 `101.09µs`라 근찾기 수렴 허용오차가 원인은 아니다. 잔여 차이를 단일 천문 모델 요인으로 완전히 분리·입증하지는 않았다.
- 120초 기준은 공식 정답이나 물리적 오차 예산이 아니라 고정 회귀 가드다. 날짜·사용 가능한 분 표기의 정확성은 공식 KASI 근거가 판정한다.
- candidate runtime과 별도 validator의 1,800개 TT root·UTC·표시 분 mismatch는 0이고 전·정확·후 5,400개 경계 배정도 mismatch 0이다. 이는 구현 결합의 일관성이지 strict release나 미래 순간 정확도 승인이 아니다.
- 대운·공망·12운성·합충형파해·신강약·격국·용신·자동 해석은 제공하지 않는다.
- v1.5의 기간 계산은 `period_type=day`, 같은 `start_date`·`end_date`, `Asia/Seoul`, `12:00` 평가만 허용한다. 주·월·연, 과거 날짜, `2049-12-31` 이후, range/unknown 원국, 다른 process에서 만든 `chart_id`는 차단한다.
- `chart_id` 승인 집합은 process 메모리에만 존재해 재시작 뒤 단일 일진 호출에 재사용할 수 없다. 암호화 state가 남아 있어도 새 날짜 계산 전 원국을 다시 계산해야 한다.
- v1.2의 `sbi2_`·`sc2_`·`scs2_`·`scr2_`·`sif2_`는 HMAC 가명 식별자일 뿐 원시 출생 state를 암호화하지 않는다. 앱 연결에는 별도의 저장 암호화와 보존·삭제 정책이 필요하다.
- HMAC key를 교체하면 기존 v2 세션 ID와 fingerprint는 무효가 되며 세션을 재계산해야 한다. v2 도입 전 확인된 runtime 세션은 0건이라 legacy ID migration은 수행하지 않았다.
- 과거 `session_state_schema_v2.json`에는 최상위 `period` key가 중복돼 일반 JSON parser가 상세 계약을 약한 object 계약으로 덮어쓰는 결함이 있다. 과거 파일은 불변 보존하되 앱 연결에는 중복 key를 거부하는 `session_state_schema_v2.1.0.json`과 `intake_registry-v1.1.0.json`만 사용한다.
- 구조화 intake FSM v1.1은 slot·provenance·입력 fingerprint를 재검증하고 현재 `scr2_` HMAC call ID와 일치하는 tool 결과만 받는다. 이 call ID는 결과 내용의 전문적 정확성을 보증하지 않으며 앱 adapter가 요청과 응답에 그대로 결합해야 한다.
- v1.4 adapter local 130/130과 v1.9 dashboard production HTTP 100/100·GPU 1쌍을 통과했다. v1.11 원국+단일 일진 binding도 별도 actual HTTP 10축과 KI20 단독·K0↔KI20 순차 Grounding Gate를 통과했지만 설정 기본값은 off이고 병합 전 기존 v1.9 운영 process는 변경하지 않았다. 이 결과를 모델의 명리 의미 품질이나 strict/full runtime 승인으로 해석하지 않는다.
- 기존 MIX20K-v3.1 생성기는 chart 4,350회와 period 900회를 같은 full release로 재생성하는 계약이다. v1.5가 승인한 것은 단일 `day`뿐이므로 전체 period를 재생성할 입력 자격이 없으며 v3.1 데이터 생성·preflight는 실행하지 않았다.
- 기존 dashboard v1.8 runtime canary는 과거 v1.1 소비 경로다. v1.4/v1.5 제한 release·권한 계약을 소비하지 않으며 현재 승인 근거로 사용할 수 없다.
- 분리 HMAC/AEAD key, AES-256-GCM persistence, 30분 retention·삭제, 2-key rotation과 dashboard의 Host·Origin·CSRF·rate limit·로그 비노출 경계는 구현됐다. 공개 앱 접근을 허용해도 key 자체를 공개하지 않는다. runtime과 대화 연결은 versioned dashboard의 명시 flag와 사용자 연결 버튼이 모두 있을 때만 열며 기본 경로로 자동 활성화하지 않는다.
