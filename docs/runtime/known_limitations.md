<!-- known_limitations.md - 후보 만세력 runtime의 지원 한계와 사용자-facing 차단 조건을 기록한다. -->

# Runtime 알려진 한계

- 대한민국 외 출생과 `Asia/Seoul` 외 timezone은 지원하지 않는다.
- 1900~2049 밖의 날짜는 지원하지 않는다.
- 자정을 넘는 단일 시간 범위는 지원하지 않는다.
- 생시 미상·범위 입력은 단일 chart로 확정하지 않는다.
- DST gap은 차단하고 fold는 후보를 유지한다.
- 음양력 provider와 절입 provider는 공식 전수 Gate 전 후보 상태다.
- KASI 자격 증명은 저장소에 없으며 공식 전수 수집은 아직 실행하지 않았다.
- 1964년 백로 1건은 Astronomy Engine과 Skyfield가 서로 다른 한국 날짜를 반환한다. 해당 KASI 24절기 공식 행이 아직 없어 어느 쪽도 판정자로 삼지 않고 미해결 Gate로 차단한다.
- KASI 달력자료는 초를 공개하지 않는 분 표기다. v1.2는 84건에서 Skyfield가 84/84 일치한 관측을 근거로 KST 최근접 분·30초 half-up을 프로젝트 등가 규칙으로 쓰며, 이를 KASI 공식 반올림 규정이라고 주장하지 않는다.
- 이 등가 규칙에서 Astronomy Engine은 84건 중 16건이 표시 분과 다르므로 현재 production 절입 provider로 승인되지 않았다.
- Astronomy Engine과 Skyfield/JPL DE440s의 1,800건 차이는 최대 80.666231초다. ΔT 모델을 맞춘 진단 뒤에도 평균 절대 차이가 100.11913% 남아 ΔT는 주원인이 아니며, 나머지는 천문 모델 차이와 일치하지만 완전히 분리·입증하지 못했다.
- 120초 기준은 공식 정답이나 물리적 오차 예산이 아니라 고정 회귀 가드다. 날짜·사용 가능한 분 표기의 정확성은 공식 KASI 근거가 판정한다.
- `internal_profile_boundary_assignment_checks` 5,400건은 runtime이 만든 경계 순간 전후의 배정 로직만 검증하며 그 순간 자체의 천문 정확성을 검증하지 않는다.
- 대운·공망·12운성·합충형파해·신강약·격국·용신·자동 해석은 제공하지 않는다.
- 기간 결과는 날짜·간지 경계만 제공하며 길흉이나 미래 사건을 뜻하지 않는다.
- `chart_id` cache는 process 메모리에만 존재해 재시작 뒤 period 호출에 재사용할 수 없다.
- v1.2의 `sbi2_`·`sc2_`·`scs2_`·`scr2_`·`sif2_`는 HMAC 가명 식별자일 뿐 원시 출생 state를 암호화하지 않는다. 앱 연결에는 별도의 저장 암호화와 보존·삭제 정책이 필요하다.
- HMAC key를 교체하면 기존 v2 세션 ID와 fingerprint는 무효가 되며 세션을 재계산해야 한다. v2 도입 전 확인된 runtime 세션은 0건이라 legacy ID migration은 수행하지 않았다.
- 과거 `session_state_schema_v2.json`에는 최상위 `period` key가 중복돼 일반 JSON parser가 상세 계약을 약한 object 계약으로 덮어쓰는 결함이 있다. 과거 파일은 불변 보존하되 앱 연결에는 중복 key를 거부하는 `session_state_schema_v2.1.0.json`과 `intake_registry-v1.1.0.json`만 사용한다.
- 구조화 intake FSM v1.1은 slot·provenance·입력 fingerprint를 재검증하고 현재 `scr2_` HMAC call ID와 일치하는 tool 결과만 받는다. 이 call ID는 결과 내용의 전문적 정확성을 보증하지 않으며 앱 adapter가 요청과 응답에 그대로 결합해야 한다.
- 구조화 intake FSM의 합성 app Gate는 100/100이고 계산된 구조·변조 검사도 모두 통과하지만 실제 앱 adapter·암호화 저장소 통합 검사는 아직 없다. `app_integration_allowed=false`이며 기존 KI20 모델의 `required_handoff_action` 평가는 14/100 그대로다. FSM 통과를 모델 대화능력 개선으로 해석하지 않는다.
- 미래 MIX20K-v3.1 생성기는 v1.2 release와 production HMAC key가 모두 준비되기 전에는 기존 20K source도 읽지 않는다. 현재 release가 없어 v3.1 데이터 생성·preflight는 실행하지 않았다.
- 기존 dashboard v1.8 runtime canary는 v1.1 소비 경로다. v1.2 FSM·key·persistence 통합 검증 전에는 v1.2 승인 경로로 사용하지 않는다.
- Runtime Gate 통과 전 대시보드나 앱의 기본 경로에 연결하지 않는다.
