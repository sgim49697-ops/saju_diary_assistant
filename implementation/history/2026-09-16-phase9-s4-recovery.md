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

<a id="phase9"></a>
## 실제 실행·검증 완료

**Phase 9/S4 완료, 제품 후보 미채택.** 수정 소스 `82a67afeeb545ba322bd77d6f31fa42265e530f4`를 한국어 커밋·`origin/master` 푸시한 뒤 `v1.1.0/build-1f851d69a91f`를 단일 실행했다. 192요청 = **172생성 + 20사전 차단**, 오류·timeout·재시도·재사용 0이다. 각 조건은 48요청·43생성·5차단이다. 최초 두 요청은 각 모델의 최장 적격 입력이며 본 예산에 포함한다. CPU 사전 검증 172건은 모델 호출로 세지 않는다.

내장 `verify`와 별도 오프라인 `verify --build build-1f851d69a91f`로 입력 재구성·모델/코드/환경 identity·원시 입력/응답·정상 종료 증거·공개 집계/manifest 전량을 재검증했다. 원문·token 배열·worker 로그는 private 경로에서만 보존한다. 공개 파일은 다음 세 개뿐이다.

- [aggregate.json](../../data/reports/saju_1b_baseline/system-context-s4/v1.1.0/build-1f851d69a91f/aggregate.json): `dcdf9e33e14d3b1f70b80f986afbc384cab09e722e675268a1d14ab593dfb45f`.
- [build_manifest.json](../../data/reports/saju_1b_baseline/system-context-s4/v1.1.0/build-1f851d69a91f/build_manifest.json): `61122bd326762fb9acb98389ac3fab0876bef5dcbf300612c0f3e218a9a2e29a`.
- [verification.json](../../data/reports/saju_1b_baseline/system-context-s4/v1.1.0/build-1f851d69a91f/verification.json): `dcb77d3d89b6087d342a2f8e0f04256c19ab918966cb42c6e05e33e0dce0b7da`.

입력 최대 1,692 token·이력 삭제 0이며 모델 사이 메시지 의미뿐 아니라 token ID도 이번 snapshot에서는 96/96 대응쌍이 같았다. 독립 tokenizer 로딩·검증 결과이지 모든 모델에 token 동일성을 요구한다는 뜻은 아니다. 이전 실패 1건은 그대로 보존했고 총 소비는 **631/680**, 현재 잔여는 **49 = S6 48 + 공유 여유 1**이다. 잔여는 후속 실행 승인이 아니다.

## 결과: 개선과 회귀를 함께 본다

다음 표는 유한 scorer v1.2의 **PASS/FAIL/UNSCORABLE** 수다. 모든 답변의 정확도나 의미 품질 점수가 아니다. 각 지표의 적용 분모를 유지하고 판정 불가를 실패로 합치지 않았다.

| 지표(적용 사례) | K0 FULL | K0 MIN | 3B FULL | 3B MIN |
|---|---:|---:|---:|---:|
| 필수 사실 사용(24) | 3/20/1 | 2/20/2 | 3/15/6 | 8/11/5 |
| 정정 상태 사용(6) | 0/6/0 | 1/5/0 | 3/2/1 | 5/1/0 |
| 틀린 전제 교정(6) | 0/6/0 | 0/6/0 | 0/6/0 | 0/5/1 |
| 두 문장(7) | 2/5/0 | 2/5/0 | 4/3/0 | 4/3/0 |
| 불필요한 사주 삽입 없음(13) | 13/0/0 | 12/1/0 | 9/4/0 | 11/2/0 |
| 불확실성 유지(5) | 4/0/1 | 4/1/0 | 3/1/1 | 4/1/0 |
| 선택 날짜 사용(2) | 2/0/0 | 1/1/0 | 1/1/0 | 2/0/0 |
| 출력 상한 도달 없음(43) | 43/0/0 | 43/0/0 | 43/0/0 | 42/1/0 |

- **K0→3B:** 필수 사실에서 FULL은 FAIL→PASS 3·PASS→FAIL 2이고 MIN은 7·1이다. FULL의 PASS 총수 3→3만으로 변화가 없다고 할 수도, FAIL 감소만으로 전량 개선이라고 할 수도 없다. 판정 불가 전이가 함께 있다. 정정 상태·형식은 개선됐지만 불필요한 사주 삽입은 FULL 0→4건, MIN 1→2건으로 늘었다. 틀린 전제 교정 PASS는 네 조건 모두 0이다.
- **FULL→MIN:** 필수 사실에서 K0는 개선 1·회귀 2, 3B는 개선 3·회귀 0이며 3B의 판정 불가→PASS 2도 별도다. 3B의 일반 요청 사주 삽입은 개선 3·회귀 1로 순감했지만 없어지지 않았고, MIN에서 장문 반복 1건도 발생했다. 정보 축소가 항상 좋다는 결과가 아니다.
- **공통 판정 가능 2×2:** 필수 사실 24개 중 네 조건 모두 판정 가능한 **15개**, 제외 9개에서 K0→3B PASS 순증은 FULL +2·MIN +5, 차이는 -3이다. 정정 상태는 공통 5/6개에서 FULL/MIN 각각 +3, 두 문장은 공통 7개에서 각각 +2, 사주 삽입 없음은 공통 13개에서 -4/-1이다. 이는 작은 개발 표본의 서술 비교이며 통계적 우월성·순수 크기 인과 효과가 아니다.

## 원응답 대조와 검사 한계

질문이나 모델 원문을 공개하지 않고 합성 case ID와 오류 유형만 남긴다. 점수·P0·scorer를 사후 수정하거나 추가 모델 호출을 하지 않았다.

- `facts-1`: K0 FULL은 다른 기둥의 천간을 일간으로 사용했고, 3B FULL/MIN은 일간을 맞히면서 일주를 지지 한 글자로 설명했다. 3B 교체만으로 사실·개념 혼동이 해소되지 않는다.
- `premise-1`: FULL의 두 모델 모두 사용자 틀린 전제에 동조했다. 3B MIN은 현재 사실·틀린 전제·추가 조정 제안을 혼합했다. 불변 서버 사실을 사용자의 주장에 맞춰 바꿀 수 있다고 안내한 부분도 제품상 오류다.
- `correction-1`: 3B MIN은 정정된 필드 값은 맞혀 PASS였지만 생일 정정으로 원국이 바뀌지 않는다는 잘못된 설명을 덧붙였다. **필드 일치 PASS는 설명 전체의 의미 정답이 아니다.**
- `general-1`, `general-3`, `general-4`, `history-4/5`: 사주 제외 요청에도 해석·원국 제한 안내를 끼워 넣는 실제 실패를 확인했다. `history-5` 3B FULL은 두 문장 형식은 맞지만 요청한 일반 확인 자료가 아니라 원국 사실을 전달하겠다고 답했다. 형식 PASS와 요청 수행을 분리한다.
- `uncertainty-4` 3B MIN은 불확실성 설명 뒤 JSON 필드 경로를 반복해 **4,096 token·133.367초·max_tokens**로 종료됐다. 불확실성 지표 PASS·종료 지표 FAIL이며, 정상 process 종료/저장은 내용 품질 통과가 아니다.
- `facts-2` 3B FULL처럼 한글 독음과 한자를 혼용한 잘못된 일주 답이 유한 파서에서 UNSCORABLE로 남는 사례도 있다. 판정 불가를 정확한 답으로 세지 않는다. 자연스러움·전체 의미 품질은 계속 `not_measured`이며 새 사람 평가 조건을 추가하지 않는다.

## 자원 비용과 다음 최소 범위

| 비용 | K0 FULL | K0 MIN | 3B FULL | 3B MIN |
|---|---:|---:|---:|---:|
| 평균 입력 token(48요청) | 1,344.979 | 884.062 | 1,344.979 | 884.062 |
| 평균 출력 token(43생성) | 40.233 | 36.791 | 80.186 | 171.698 |
| 평균 cold 지연(초) | 7.704 | 7.345 | 8.171 | 10.958 |
| 최대 cold 지연(초) | 11.133 | 13.997 | 12.301 | 133.367 |
| 최대 CUDA reserved VRAM(MiB) | 3,072 | 2,950 | 6,526 | 6,812 |

지연은 worker 내부 모델 로딩·생성을 포함하며 부모의 파일 검증·CPU 준비·전체 wall-clock이나 warm 앱 응답 속도와 같지 않다. 3B는 동일 BF16·offload/adapter 없음으로 실행 가능했으나 메모리 비용과 긴 반복 출력 위험이 더 컸다.

이번 **1.3B→3B 비교만으로 모든 오류가 해결되지 않았음**은 확인했다. 크기와 무관하다고 확정하거나 데이터셋만의 문제라고 결론 내릴 수는 없다. K0/3B는 구조·가지치기·증류·학습 이력도 다르며 R16·운영 KI20·미시험 3B/P1과의 직접 비교가 아니다. 총점을 만들어 승자를 고르지 않고 `candidate_selected=false`를 유지한다.

다음 착수 대상은 기존 [Phase 10](../plans/saju_product_roadmap/phases/phase-10.md)이다. 유일한 승인 필드의 직접 사실 응답, 일반 대화에서 무관한 사주 정보/지시 제외, 정정된 현재 사실과 과거 모델 발언 분리를 **기존 세션·binding 계약 안의 최소 제품 후보 하나**로 다룬다. 세션 전면 개편이나 대형 라우터를 추가하지 않는다. 모델·지시문·정보 선택을 결합한 후보의 실제 성능은 새 확인 단계 전까지 미측정이다. Phase 11 가설→12 후보 확인→필요할 때만 13 학습의 순서는 그대로이며 이번에는 Phase 10 이후·추가 학습·앱 배포를 실행하지 않았다. 운영 PID 3144071·재시작 0·코드 `0e77621`과 계산 release·Phase 6·소비된 sealed blind를 보존했다.

## 진행 기록

- 2026-09-16 최종 검증: 실제 비교 완료 후 문서 **47개/0.497초**, 전체 unittest **1,074개/137.270초**, Ruff·diff 검사가 모두 통과했다. 문서 1개에서 변경 전 도입부 문장 기대가 남아 처음 실패한 것은 현재 완료 서술과 증거 검증으로 갱신했다. M-0293·O-0173의 새 완료 근거, 공개 고정 hash·192/172/20 분모·공통 판정 가능 15/24·후보 미채택·후속 미실행 보호를 추가했고 이전 실패 증거 검사는 유지했다. 원문 3개·588행 대응·정본 정책 검증도 통과했다.
- 2026-09-16 보존 점검: S4 내장·별도 verify, 기존 S3 `build-ffd985905b51`·8A `build-49b9aed70565` 별도 verify가 불변 공개 hash로 통과했다. 실행 시 동결 source fingerprint와 현재 소스가 같고 기존 실패의 모든 pin도 유지됐다. 새 생성 172건의 시작/정상 종료/로그 증거가 각각 172개이며 재사용 0이다. GPU 계산 process 없음·운영 PID 3144071/재시작 0을 확인했다. 원시 출력·모델·사용자 ZIP·다른 세션과 worktree는 변경/추적하지 않았다. 검증된 공개 3파일·상태 문서·문서 회귀만 원본 `master`의 후속 한국어 커밋·푸시 대상으로 삼는다.

- 2026-09-16 실행 전 검증: 관련 **73개/45.001초**, 문서 **46개/0.478초**, 전체 unittest **1,073개/141.202초**가 실패·오류·건너뜀 없이 통과했다. Ruff·diff·계약·plan도 통과했다. 최종 오프라인 dry-run은 `build-1f851d69a91f`, 192요청·사전 차단 20·최대 입력 1,692 token·실제 CPU 소비 재생 172/172·모델 호출 0이며 receipt hash는 `bcf8a6be16bf5e4c747110961bc42ee47e3677de58f0280a899bc23ce24e0ce6`이다. 수정 중 사전 dry-run의 `build-95cba5868eb1`은 실행하지 않았으며 raw/원장도 생성하지 않았다. S3·8A 별도 verify가 기존 공개 hash로 통과했고 운영 PID 3144071·재시작 0을 유지했다. 소스 체크포인트 이후 새 build만 실제 실행 대상으로 삼는다.

- 2026-09-16 수정: 증명된 tokenizer 별칭과 실제 소비 사전 검증, v1.1 별도 경로·실패 증거 연결·예산 산술을 구현했다. 최초 Ruff는 import 순서 4건을 지적해 해당 파일만 정렬했다. 첫 관련 시험은 73개 중 서로 다른 합성 tokenizer를 사용하는 projection 단위 시험 1개가 실제 소비 검사에서 거부됐다. projection 시험과 실제 소비 통합 회귀를 구분했고 실제 거부 검사는 유지했다. 후속 검증·GPU 결과는 확인 후 기록한다. 이 시점에 실제 새 비교는 아직 미실행이다.
