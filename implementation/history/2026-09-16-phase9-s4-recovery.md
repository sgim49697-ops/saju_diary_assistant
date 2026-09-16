<!-- 2026-09-16-phase9-s4-recovery.md - S4 저장 식별자 수정·실제 CPU 소비 검증·승인된 새 버전 실행을 기록한다. -->

# Phase 9 / S4 저장 호환성 수정과 재실행

## 승인 범위

이전 [첫 실행 중단](2026-09-16-phase9-s4-execution.md#blocked-run) 뒤 사용자에게 저장 연결 결함·실패 1요청 보존·공유 여유 1요청 사용을 설명했다. 사용자가 “무슨 문제야? 다시 진행 해”라고 요청해 **호환성 수정 → 실제 CPU 저장 검증 → 새 버전 전체 192요청**을 승인했다. 새 Phase나 관리 체계가 아니라 기존 Phase 9/S4의 복구다.

기존 사용 438 + 실패 1 + 새 비교 192 = 완료 시 누적 **631/680**, 잔여 **49 = S6 48 + 공유 여유 1**이다. 새 비교는 최대 172생성과 기존 사전 차단 20이며 CPU 합성 소비 검증은 모델 호출 0이다. 학습·데이터 보정·운영 모델 교체·앱 통합·새 24문항·sealed blind 사용은 이번 범위에 없다.

## 수정과 불변 경계

- 이전 `v1.0.0/build-296dffd1ef51`의 raw·실패 응답·시작/종료/로그·예산 원장 및 v1.0 계약은 그대로 보존한다. 당시 실행 소스는 `7e7819e`, 중단 보고는 `31da049`에서 복구할 수 있다. 현재 CLI는 별도 `v1.1.0` 계약·raw/public 경로를 사용한다. 이전 증거 전체의 고정 hash·파일 집합을 재검증해 삭제·추가 요청·변조를 거부한다.
- S4 모델별 tokenizer identity를 원응답에 보존한다. 현재 두 모델의 tokenizer 3파일·실제 backend·chat template·해당 요청의 렌더링/토큰이 canonical과 같은 경우에만 **CPU 저장 사본**의 revision을 v1.15 별칭으로 연결한다. 다른 tokenizer를 이름만 바꿔 통과시키지 않는다. 증명을 응답과 공개 검증 사슬에 남긴다.
- 동결 v1.15 저장 검사, 기존 부모/S3 코드, P0·질문·facts·이력·모델 pin·생성 인자·scorer v1.2는 수정하지 않는다. 운영 서비스도 변경하지 않는다. K0 저장 슬롯을 사용하는 진단 재생이지 3B 앱 연결이 아니다.
- 준비/dry-run에서 생성 예정 **172건 전량의 실제 저장/API 코드**를 합성 문장으로 실행한다. GPU 호출은 없고 임시 저장소는 종료 시 정리된다. 이 결과를 동결 build identity에 포함해 사전 검사 실패 시 GPU 실행을 막는다.
- 회귀는 실제 소비 코드의 원래 S4 revision 거부와 증명된 별칭 통과, 원응답 무변경, 파일/template/token/backend/identity 변조 거부, 기존 실패 보존·예산 산술을 검사한다. 독립 projection 단위 시험의 서로 다른 합성 tokenizer는 별도로 유지하며 이를 실제 저장 호환성 증거로 사용하지 않는다.

## 검증·실행 순서

```bash
uvx ruff check scripts tests
.venv/bin/python -B -m unittest tests.test_system_context_s4 tests.test_system_context_s4_audit tests.test_system_context_s4_consumers tests.test_system_context_scoring_v1_2 -q -b
.venv/bin/python -B -m unittest discover -s tests -q -b
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s4 validate-contract
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s4 plan
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -B -m scripts.evaluation.system_context_s4 execute
git diff --check
```

검증된 소스 체크포인트 이후 dry-run의 새 build ID를 확인한다. 기존 검증 CPATH·native JIT·오프라인 환경과 `SYSTEM_CONTEXT_S4=K0_KANANA3B_P0_V1` 아래 `execute --execute --build <새 build ID>`를 단일 실행하고, 종료 후 동일 CLI의 `verify --build <새 build ID>`로 전량 재계산한다. 이미 수집한 모델은 다시 다운로드하지 않는다. GPU 유휴·여유 VRAM 12GiB·운영 서비스 불변을 계속 검사한다. 추가 실패는 숨기거나 자동 재생성하지 않는다.

## 진행 기록

- 2026-09-16 실행 전 검증: 관련 **73개/45.001초**, 문서 **46개/0.478초**, 전체 unittest **1,073개/141.202초**가 실패·오류·건너뜀 없이 통과했다. Ruff·diff·계약·plan도 통과했다. 최종 오프라인 dry-run은 `build-1f851d69a91f`, 192요청·사전 차단 20·최대 입력 1,692 token·실제 CPU 소비 재생 172/172·모델 호출 0이며 receipt hash는 `bcf8a6be16bf5e4c747110961bc42ee47e3677de58f0280a899bc23ce24e0ce6`이다. 수정 중 사전 dry-run의 `build-95cba5868eb1`은 실행하지 않았으며 raw/원장도 생성하지 않았다. S3·8A 별도 verify가 기존 공개 hash로 통과했고 운영 PID 3144071·재시작 0을 유지했다. 소스 체크포인트 이후 새 build만 실제 실행 대상으로 삼는다.

- 2026-09-16 수정: 증명된 tokenizer 별칭과 실제 소비 사전 검증, v1.1 별도 경로·실패 증거 연결·예산 산술을 구현했다. 최초 Ruff는 import 순서 4건을 지적해 해당 파일만 정렬했다. 첫 관련 시험은 73개 중 서로 다른 합성 tokenizer를 사용하는 projection 단위 시험 1개가 실제 소비 검사에서 거부됐다. projection 시험과 실제 소비 통합 회귀를 구분했고 실제 거부 검사는 유지했다. 후속 검증·GPU 결과는 확인 후 기록한다. 이 시점에 실제 새 비교는 아직 미실행이다.
