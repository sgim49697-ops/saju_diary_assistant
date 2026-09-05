<!-- 40-day-relation-runtime.md - 원국과 단일 날짜 간의 결정론적 관계 존재만 계산하는 release를 정의한다. -->

# 40. 단일 날짜 Relation v1

이 문서는 2026-09-02 완료 구현의 계약·재현 이력이다. 아래 v1.11 process 보존은 당시 작업 범위이며 현재 운영 상태는 [00 기준선](00-current-baseline.md)을 따른다. 후속 실행 순서는 [로드맵 README](README.md)의 50-A부터이며 이 후보를 자동 재실행·배포하지 않는다.

## 상태

`완료` — `saju-natal-day-relation-release-v1.0.0-554bb9bfaea9`, dashboard v1.13 자동 canary `build-eaeeb35866d1`

## 포함

- 기간 연·월·일 천간의 일간 기준 십신.
- 기간 연·월·일 지지 본기의 일간 기준 십신.
- 원국 각 지지와 기간 일지 사이 합·충·형·파·해 존재.
- 동일 천간·지지 반복.
- `PROFILE_DETERMINISTIC`, table version, parent chart/period snapshot hash.

## 제외

- 관계 우선순위와 합화 성립 단정.
- 길흉 점수, 신강약, 격국, 용신.
- 이별·사고·합격 등 사건 변환.
- 2~31일 relation 배열.

## Gate

- 천간 십신 10×10과 지지 본기 10×12 전수 일치.
- 합·충·해·파 pair 대칭성, 형 group·자형의 명시적 방향 규칙 검증.
- 존재하지 않는 관계 생성 0, interpretation field 0.
- 단일 날짜가 아니거나 relation release가 없으면 fail-closed.

dashboard v1.13은 v1.12를 부모로 단일 날짜에만 relation card와 model allowlist를 추가했다. 주·월 범위는 날짜 label만 유지하며 relation은 `null`이다. 이 버전은 검증된 후보이고 feature 기본값과 당시 운영 dashboard v1.11 process는 바꾸지 않았다.

## 고정 정책

- 정책 ID: `KR_NATAL_DAY_RELATIONS_V1`
- 관계표: `branch-relations-v1.0.0`
- 권한: `PROFILE_DETERMINISTIC`
- 합·충·파·해는 고정된 무방향 pair로 계산한다.
- 형은 `寅·巳·申`, `丑·戌·未`, `子·卯` 그룹의 서로 다른 모든 pair를 대칭으로 계산한다.
- 자형은 `辰·午·酉·亥`의 동일 지지 pair에만 적용한다.
- 한 pair에 둘 이상의 관계가 있으면 모두 보존한다. 우선순위나 합화 성립은 계산하지 않는다.
- 동일 간·지는 기간 연·월·일 각각과 원국 년·월·일·시를 전수 비교한다.

## 구현·산출물

- 계약: `configs/runtime/relations/registry-v1.0.0.json`
- 정책: `configs/runtime/relations/relation_policy-v1.0.0.json`
- 공개 결과: `configs/runtime/relations/relation_output_schema-v1.0.0.json`
- Runtime: `scripts/runtime/relation_v1/engine.py`
- Dashboard binding: `scripts/runtime/relation_dashboard_binding.py`
- Dashboard v1.13: `scripts/training/phase5_dashboard_v1_13.py`
- 자동 Gate: `scripts/evaluation/saju_runtime/relation_conformance_v1.py`
- 공개 report: `data/reports/saju_relation_conformance/v1.0.0/build-aa13a1333586`
- release: `configs/runtime/relations/releases/v1.0.0/release_registry.json`
- Dashboard 자동 canary: `scripts/evaluation/saju_runtime/relation_dashboard_canary_v1.py`
- Dashboard 공개 report: `data/reports/saju_relation_dashboard_canary/v1.0.0/build-eaeeb35866d1`

공개 결과는 원국·기간 사실 snapshot SHA-256을 부모로 기록하지만 `chart_id`, `period_id`, `relation_snapshot_id`, 출생 원문은 내보내지 않는다. 관계 존재·반복·기간 십신 외의 해석·점수·사건 예측 field도 허용하지 않는다.

## 실행 순서

```bash
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_conformance_v1 validate-contract
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_conformance_v1 plan
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_conformance_v1 execute
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_conformance_v1 execute --execute
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_conformance_v1 verify

.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_release_registry_v1 verify

.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_dashboard_canary_v1 validate-contract
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_dashboard_canary_v1 plan
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_dashboard_canary_v1 execute
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_dashboard_canary_v1 execute --execute
.venv-data/bin/python -m scripts.evaluation.saju_runtime.relation_dashboard_canary_v1 verify \
  --report-root data/reports/saju_relation_dashboard_canary/v1.0.0/build-eaeeb35866d1
```

## 진행 기록

### 2026-09-05 — 완료 이력과 현재 운영 포인터 구분

- 당시 v1.11 보존 문구를 현재 service 상태로 오해하지 않도록 00 정본을 연결했다. 기존 정책·release·canary는 보존했다. 문서 검증 결과는 [재정렬 기록](../../history/2026-09-05-model-cause-roadmap.md)을 따른다.

### 2026-09-02 — 단일 날짜 relation release 승인

- 기간 연·월·일 천간과 지지 본기의 일간 기준 십신, 기간 일지와 원국 네 지지의 합·충·형·파·해 존재, 동일 간·지 반복을 별도 engine으로 구현했다.
- 원국 v1.5와 기간 v1.0의 release·snapshot hash·기간 HMAC을 다시 검증하고 단일 날짜가 아니면 차단한다. 공개 projection에는 내부 ID·출생값·해석 field가 없다.
- conformance `build-aa13a1333586`에서 천간 십신 100, 지지 본기 십신 120, 지지 pair 144를 전수 검증해 누락·초과·대칭·형 방향 mismatch 0을 확인했다.
- 실제 승인 v1.5 원국 → 기간 v1.0 → relation v1 연결 smoke와 release 결합 test를 통과했다.
- dashboard v1.13, strict/full, sealed blind, MIX20K-v3.1, 학습·모델 승격은 실행하거나 승인하지 않았다.

### 2026-09-02 — Dashboard v1.13 relation 연결 검증

- v1.12의 원국·기간 session을 부모로 하는 별도 v1.13 binding을 추가했다. 단일 날짜에서는 기간 십신·직접 관계·동일 간지를 원국·기간 snapshot hash에 묶고, 2~31일 범위에서는 relation을 저장·표시·model binding하지 않는다.
- 관계 카드와 model allowlist를 추가하되 해석, 관계 우선순위, 합화 성립, 길흉 점수, 사건 예측은 전달하지 않는다. K0와 KI20에는 같은 canonical snapshot을 전달한다.
- 자동 canary `build-eaeeb35866d1`에서 계획된 8개 stratum 160/160을 통과했다. 단일 날짜 30, 범위 relation 미생성 20, 중첩 관계·형 30, 실제 암호화 session 재시작 20, 변조 차단 20, 동일 context 10, 공개 누출 20을 확인했다.
- commit 전 whitespace 검사에서 relation core의 EOF 공백을 정리한 뒤 conformance `build-aa13a1333586`, release `saju-natal-day-relation-release-v1.0.0-554bb9bfaea9`, dashboard canary `build-eaeeb35866d1` 순서로 전체 hash chain을 다시 발행했다.
- 추가 회귀 점검에서 비활성 status가 부모 버전 `1.2.0`을 반환하던 복사 잔재를 찾아 `1.3.0`으로 교정하고 해당 검사를 canary와 unittest에 고정했다.
- 전체 Ruff와 Node 구문 검사, relation·dashboard v1.9~v1.13·기간 표적 unittest 94건, relation conformance·release·dashboard canary 재검증을 통과했다. 전체 608건도 실행했으나 격리 worktree에 Git 제외 원천·과거 파생 build·고정 모델을 배치하지 않아 1 failure·21 errors가 남았고, 해당 private 자원을 복제하거나 sealed blind를 열지 않았다.
- feature는 기본 off이고 현재 dashboard v1.11 운영 process를 교체하지 않았다. GPU 생성, sealed blind 접근, strict/full 승인, MIX20K-v3.1, 학습·모델 승격도 수행하지 않았다.
