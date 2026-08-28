<!-- ssaju_review.md - golbin/ssaju 고정 revision의 계산 정책·데이터 충돌·도입 가능성을 기록한다. -->

# `golbin/ssaju` 외부 참고 구현 검토

## 결론

도입 결론은 **일부 모듈만 참고 구현**이다. `ssaju`를 dependency, submodule, runtime oracle로 그대로 넣지 않는다. 천간·지지·오행·음양·십신 대응 구조처럼 작은 순수 계산 단위는 독립 Gold fixture를 통과한 뒤 clean-room 성격의 프로젝트 구현에 참고할 수 있지만, 생년월일시→원국 전체와 합충형파해 전체를 현재 상태 그대로 정답 엔진으로 채택할 근거는 부족하다.

특히 신강약 점수, 격국, 용신, 관계 우선순위와 자동 해석은 구현상 휴리스틱이다. 이 값들은 전문가 Gold 학습·평가 label로 사용하지 않도록 [`configs/saju_calculation_policy.json`](../../configs/saju_calculation_policy.json)에서 `heuristic_only` 또는 `generated_interpretation`, `qa_gold_candidate=false`, `validator_mode=disabled`로 차단했다.

이번 검토는 advisory 비교만 수행했다. 기존 canonical MIX20K, staging record, eval, Phase 4 Gate, `training_promotion_allowed=true` 상태를 바꾸지 않았고 Phase 5 학습도 실행하지 않았다.

## 검토 기준점과 재현 정보

- 저장소: [`golbin/ssaju`](https://github.com/golbin/ssaju)
- 검토 시점 `main` HEAD: [`07b608a778be6dac8669e04b9ab794c441959208`](https://github.com/golbin/ssaju/tree/07b608a778be6dac8669e04b9ab794c441959208)
- commit 시각: `2026-04-20T12:04:40+09:00`
- package: `ssaju@0.2.0`, Node `>=18`, runtime dependency 0개
- 로컬 검증 환경: Python `3.10.12`, Node `v22.22.0`, npm `10.9.4`
- build `dist/index.mjs` SHA-256: `f0b08c64bfe7095d615766084ca9a27da61e9cc0a0f4456408de069b964eaaee`
- 비교 review: [`review-c1e129b1e602`](../../data/reports/saju_1b_baseline/ssaju-policy-review/v1.0.0/review-c1e129b1e602/build_manifest.json)
- review SHA-256: `c1e129b1e602d0ba3cad2a534efec18045b5c6ce401ebe40c2096fbc398cdf83`

2026-08-29에 원격 `refs/heads/main`과 read-only 임시 checkout의 `HEAD`가 위 SHA로 같은 것을 다시 확인했다. 프로젝트에는 외부 source, dependency, submodule을 추가하지 않았다.

### 중점 파일 SHA-256

| 파일 | SHA-256 |
|---|---|
| `LICENSE` | `237d62618b9d436054ad0dfdd53a93b6cdea1a3d4b62abb313b9f455c8c7e48a` |
| `README.md` | `ad2e0f348fdc7100172ba63b09354e2762271106755208ec271c7c25163fe83d` |
| `package.json` | `a2450e41149c2e1f558181f4a42f13b86794319d1006db8248c66bd0d1f4d206` |
| `package-lock.json` | `e5a5aca7a2360db0365364e24fe279f27351569e7eb0fd843c003c2ca5aa5de8` |
| `src/constants.ts` | `4402529a5efe252d89377782ef92a2fd7d62d162c33e73eb2c38e679aaac60ad` |
| `src/manse.ts` | `ba1c32fb9f3ea02aac80e6fe7de79b4c9439fe01639562fae2ad0f1b65b6bfec` |
| `src/analyze.ts` | `5238c3e2dfbf2358406d32cc0e36bd83de618e27f49e8cd6938cd64c128fc7c3` |
| `src/format.ts` | `df0cb2b40284d3e794debf1a235200fc8206fb9a29061787bd100f40b4bd761d` |
| `tests/calculateSaju.test.ts` | `22c3e90e19027a82e75d21f4ee3f2b92fd70e5ddd363c28d509d93ac31df3971` |

### 프로젝트 입력 고정점

- canonical Phase 4 build: `build-a1a34616dd72`
- canonical MIX20K SHA-256: `a61c16dc65ad24805b293ad50404d519c68d1ae844419b18c9e1538ea7a5bc3a`
- staging build: `build-847088ee804d`
- Nemotron staging SHA-256: `0242bad3b408e9143813bb94fc84ad2911146f8262f93a7a924f20c06d32d132`
- bazi staging SHA-256: `80654a812d41dffeea80ea72a02c3101d24bc5ee694e4229639fcd12960f2965`
- Nemotron raw source revision: `ffb934248746a2dea64ef771c0d86e1743d25702`
- Nemotron raw manifest SHA-256: `df200cd8faf366bcc45d595b5d1c0a9f2ff422df5c33aaf2b5d71406938dc383`
- raw 22파일·parquet 20파일 전수 해시 집합 SHA-256: `24bc0767080ebec45515ec873f1ba8b0adcafac87435c93975fc3bf4e2769f51`

## 파일별 코드 검토

### `src/constants.ts`

10천간·12지지, 오행·음양, 천간 십신표와 지장간을 명시적 상수로 둔다. [십신표](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/constants.ts#L112-L233)와 [지장간 여기·중기·정기표](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/constants.ts#L235-L248)는 검토·이식 단위가 명확하다는 장점이 있다. 다만 지장간, 12운성, 신살, 용신 후보표는 학파별 정책 차이가 있으므로 이 구현의 표만으로 Gold를 선언할 수 없다.

한국 DST 상수는 [1960·1987·1988만 포함](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/constants.ts#L9-L25)한다. IANA tzdb는 대표 지역의 역사적 시간대·DST 변화를 관리하며 주기적으로 갱신되는 자료다. 현재 `Asia/Seoul` 역사와 비교하면 1948~1951 및 1955~1959 구간이 `ssaju` 상수에서 빠져 있다. [IANA tzdb 안내](https://data.iana.org/time-zones/tz-link.html)

### `src/manse.ts`

입력 timezone, longitude, 평균태양시 적용 여부를 노출하고 양력·음력, 절입, 연주·월주·일주·시주를 한 API에서 계산한다. 그러나 다음 이유로 runtime Gold 엔진 승인을 보류한다.

- 지원 범위를 1900~2099로 검사하지만 [음양력 변환](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/manse.ts#L317-L440)은 전수 역변환에서 실패가 있다.
- 절입은 공인 ephemeris fixture가 아니라 근사 태양황경 경로다.
- 평균태양시는 경도 보정만 수행하며 균시차 계약과 별개다.
- [시주 구간](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/manse.ts#L612-L631)은 정각 기준이고, 야자시·조자시 등 정책을 선택할 수 없다.
- 한국 역사 DST가 원국 day/hour와 절입 경로에 동일한 정책으로 적용된다고 볼 수 없다.

양력 1900-01-01~2099-12-31 전체 73,049일에 `solarToLunar → lunarToSolar`을 실행한 결과는 다음과 같다.

| 결과 | 일수 |
|---|---:|
| 정상 역변환 | 70,547 |
| 잘못된 역변환 | 2,398 |
| 예외 | 104 |
| 총 실패 | 2,502 |

공식 Gold 후보는 한국천문연구원의 [음양력 정보 OpenAPI](https://www.data.go.kr/dataset/15012679/openapi.do)다. 이 서비스는 음력일·양력일·윤달·간지·율리우스적일 정보 조회를 제공한다. 향후에는 KASI 결과와 IANA 역사 시간대를 결합한 fixture를 먼저 만들고, 그 fixture로 엔진 후보를 비교해야 한다.

원 저자 저장소에도 시간 정책과 한국 서머타임 범위를 다룬 [issue #3](https://github.com/golbin/ssaju/issues/3)이 열려 있다. 2026-08-29 확인 시 상태는 `OPEN`이다.

### `src/analyze.ts`

지지 십신은 일간과 해당 지지의 [지장간 정기](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/analyze.ts#L110-L128)를 `TEN_GODS`에서 조회한다. 공망, 봉법·거법 12운성, 운 간지 계산도 정책을 분리하면 deterministic QA 후보가 될 수 있다.

반면 다음은 Gold가 아니다.

- 신강약: [50점 시작, 월지 +20, 표면 오행 +10/+8/-8, 12운성 ±15, 70/30 임계값](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/analyze.ts#L843-L882)의 임의 가중치다.
- 격국: [강약 점수 극단값과 월간 십신의 단순 분기](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/src/analyze.ts#L885-L903)다.
- 용신: 일간별 정적 후보표와 강약·격국 분기다.
- 관계 우선순위: 구현자가 부여한 관계 점수 정렬이다.
- 자동 해석: 격국·최다 오행·신살을 문장 템플릿으로 렌더링한다.

합충형파해는 규칙표가 명시적이라는 장점이 있지만, 관계별 direct assertion이 부족하고 self-punishment와 반합 범위 등 정책을 재검증해야 한다. 귀문 pair 방향 문제를 제기한 [issue #4](https://github.com/golbin/ssaju/issues/4)도 2026-08-29 현재 `OPEN`이다.

### `src/format.ts`

`toCompact()`와 `toMarkdown()`는 LLM 입력에 편리하지만 deterministic fact, 휴리스틱 신강약·격국·용신, 실행 시점의 세운·월운, 자동 해석을 한 문자열 안에 섞는다. field별 `evidence_class`, 계산 정책 ID, confidence, 기준 시각을 보존하지 않으므로 그대로 학습 prompt 또는 validator 정답으로 사용하지 않는다. 향후 앱에는 fact-only JSON serializer를 별도로 두는 것이 안전하다.

### `tests/calculateSaju.test.ts`

`npm ci`, `npm test`, `npm run typecheck`, `npm run build`를 고정 checkout에서 실행했고 테스트 21건과 typecheck가 통과했다. 단일 golden 원국, 일부 시각 경계, 음양력 예시, 12운성·신살·포맷을 검증하지만 다음 범위가 빠져 있다.

- 1900~2099 음양력 역변환 전수 검사
- 공식 KASI fixture 대조
- 한국 역사 DST 전 구간
- 합·충·형·파·해·원진·귀문 각각의 direct assertion
- 전문가 Gold 기반 지장간·십신·공망·12운성·대운 fixture

`npm audit --json`은 runtime dependency 0개인 상태에서 dev dependency tree의 `brace-expansion` high 1건과 `esbuild` low 1건, 합계 2건을 보고했다. 현재 프로젝트에 dependency를 추가하지 않았으므로 앱 runtime 공급망에는 편입되지 않았다.

## 현재 학습데이터와 정책 비교

아래 충돌 수는 canonical MIX20K에 들어간 Nemotron 11,000행과 bazi 5,000행만 대상으로 재계산했다. `not_comparable`은 0건이 아니라 현재 구조화 label이 없어 비교할 수 없다는 뜻이다.

| 필드명 | 현재 학습데이터 정책 | `ssaju` 정책 | 충돌 행 수 | 앱 runtime 권장 정책 | 기존 데이터 수정 |
|---|---|---|---:|---|---|
| 원국 4주 | Nemotron 원천 제공, 생성 세부 구현 미확정 | 근사 절입·timezone·선택적 평균태양시 | 174 진단 | KASI·IANA Gold 전까지 보류 | 자동 수정 금지 |
| 천간·지지 | 원국 4주 8자 | 같은 10천간·12지지 | 0 | 공통 상수 후보 | 불필요 |
| 음양·오행 | 표면 8자 각 1회 집계 | 같은 표면 집계 | 0 | “표면 집계” 명시 후 후보 | 불필요 |
| 지장간 | label 없음 | 여기·중기·정기 고정표 | `not_comparable` | 전문가 표 교차검증 후 후보 | 새 버전에만 추가 |
| 십신—천간 | 일간과 각 천간 생극·음양 | `TEN_GODS` 표 | 0 | 독립 fixture 후 후보 | 불필요 |
| 십신—지지 | 지지 자체 오행·음양 | 지장간 정기 기준 | 8,563 | 정기 기준 권장, Gold 전 advisory | 새 버전 이관 필요 |
| 공망 | label 없음 | 일주 순공 기준 | `not_comparable` | 독립 fixture 후 후보 | 새 버전에만 추가 |
| 12운성 | label 없음 | 봉법·거법 병행 | `not_comparable` | 봉법/거법 계약 분리 후 후보 | 새 버전에만 추가 |
| 합충형파해 | label 없음 | 고정 pair/group 탐지 | `not_comparable` | 관계별 표·fixture 후 후보 | 새 버전에만 추가 |
| 대운·세운·월운 간지 | label 없음 | 절입·성별·년간 음양·현재시각 | `not_comparable` | 시간 정책 Gold 후 간지만 후보 | 새 버전에만 추가 |
| 신강약 | bazi rule ID soft label | 표면 오행 가중 점수 | 고유 명식 594 | 둘 다 `heuristic_only` | 정답 교정 금지 |
| 격국·용신·해석 | 직접 비교 Gold 없음 | 임계값·정적 후보표·템플릿 | `not_comparable` | 전문가 Gold 금지 | 불필요 |

### 지지 십신 상세

Nemotron 11,000행의 천간 십신 44,000필드는 `ssaju`의 천간 십신표와 의미상 모두 일치했다. 일간의 `본원(일간)`만 비교 시 `비견`으로 정규화했다.

지지 십신 44,000필드는 현재 label이 `branch_surface_element_yinyang_v1`과 전부 일치했다. 원천 생성기 소스가 고정 dataset revision에 없으므로 이 정책은 코드 확인이 아니라 **44,000필드 전수 일치로 추론한 관찰 결과**다.

`branch_main_hidden_stem_v1`과 비교하면 8,563행, 13,808필드가 달랐다. 정기의 오행은 같고 음양만 반대인 `子·巳·午·亥`에서만 차이가 난다.

| 기준 | 충돌 필드 수 |
|---|---:|
| `子` | 3,489 |
| `巳` | 3,375 |
| `午` | 3,509 |
| `亥` | 3,435 |
| 년주 | 3,609 |
| 월주 | 2,881 |
| 일주 | 3,675 |
| 시주 | 3,643 |

[`conflict_samples_100.jsonl`](../../data/reports/saju_1b_baseline/ssaju-policy-review/v1.0.0/review-c1e129b1e602/conflict_samples_100.jsonl)은 네 지지별 25건씩 고유 100행을 결정론적으로 뽑았다. 원천 record ID, UUID, 내부 hash, 생년월일, 경도, 지역, persona는 포함하지 않는다.

권장 runtime 정책은 지장간 정기 기준이지만 아직 `blocked_pending_gold`다. 기존 approved build를 덮어쓰지 않으며, 전문가 정책 승인을 받은 뒤 별도 dataset/schema version에서만 migration한다.

### 원국 진단

Nemotron raw 공개 필드와 canonical 원국 11,000행을 연결해 네 가지 입력 정책으로 실제 `ssaju`를 실행했다. 이는 차이를 찾는 differential diagnostic이며 어느 쪽도 자동 정답으로 취급하지 않는다.

| 입력 시나리오 | 충돌 행 | 충돌 필드 | 년 | 월 | 일 | 시 |
|---|---:|---:|---:|---:|---:|---:|
| civil clock | 3,381 | 3,717 | 16 | 173 | 269 | 3,259 |
| longitude 평균태양시만 적용 | 888 | 996 | 18 | 182 | 73 | 723 |
| 원천 `last_datetime` 재생 | 182 | 201 | 18 | 181 | 1 | 1 |
| 문서화 정책 hybrid—년·월 civil, 일·시 LAST | 174 | 191 | 16 | 173 | 1 | 1 |

hybrid의 일주·시주 1건은 1949년 한국 DST 경계와 맞물린다. 이는 `ssaju`의 역사 시간대 보완 필요성을 강화하지만 Nemotron 원국을 무조건 Gold로 확정하는 근거는 아니다.

### bazi 신강약 상세

canonical bazi 5,000행은 1,250개 고유 명식이 질문 유형별로 4회 반복된다. 현재 rule ID class와 `ssaju` 점수 class의 비교표다.

| 현재 \ `ssaju` | neutral | strong | weak |
|---|---:|---:|---:|
| neutral | 198 | 247 | 5 |
| strong | 76 | 437 | 0 |
| weak | 218 | 48 | 21 |

고유 명식 594/1,250, 즉 47.52%가 다른 class이고 반복 행으로는 2,376행이다. 두 정책 모두 전문가 정답이 아닌 서로 다른 휴리스틱이므로 이 수치는 데이터 오류 수가 아니다. 기존 bazi label을 `ssaju` 점수로 수정하지 않는다.

## 활용안 비교

| 활용안 | 장점 | 주요 위험 | 결론 |
|---|---|---|---|
| runtime 계산 엔진 | 작고 빠르며 단일 API와 LLM 포맷 제공 | 역법 roundtrip·DST·관계표·휴리스틱 혼재 | 그대로 도입하지 않음 |
| deterministic QA 생성기 | 정책 고정 시 대량 구조화 QA 생성 가능 | 한 구현의 오류가 train/eval에 같이 복제 | Gold 통과 field만 일부 참고 구현 |
| validator | 기존 label과 정책 차이를 빠르게 탐지 | 단일 엔진을 oracle로 쓰면 학파 차이를 오류로 오판 | advisory differential validator로만 사용 |

다음 Gate는 KASI·IANA 기반 시간 fixture, 전문가 승인 정책표, fact-only serializer, 새 schema/version migration 설계다. 그 전에는 `ssaju` 기반 deterministic QA를 Gold로 생성하지 않는다.

## 라이선스와 `THIRD_PARTY_NOTICES` 후보

고정 revision은 [MIT License](https://github.com/golbin/ssaju/blob/07b608a778be6dac8669e04b9ab794c441959208/LICENSE)이며 저작권 고지는 `Copyright (c) 2026 Jin`이다. MIT 조건상 코드 또는 실질적 부분을 복제·배포할 때 저작권 고지와 허가·면책 문구를 보존해야 한다. 이 내용은 법률 자문이 아니라 고정 license 원문에 따른 프로젝트 운영 기록이다.

현재는 외부 코드를 복제하거나 의존하지 않았으므로 아래 항목은 실제 notice가 아닌 후보로만 기록한다.

```text
ssaju
Copyright (c) 2026 Jin
Source: https://github.com/golbin/ssaju
Reviewed revision: 07b608a778be6dac8669e04b9ab794c441959208
License: MIT License
Include the complete MIT license text when distributed code contains ssaju or a substantial copied portion.
```

## 재현 명령

외부 checkout은 프로젝트 밖 임시 경로에서만 준비한다.

```bash
SSAJU_REVIEW_ROOT="$(mktemp -d)/ssaju"
git clone https://github.com/golbin/ssaju.git "$SSAJU_REVIEW_ROOT"
git -C "$SSAJU_REVIEW_ROOT" checkout --detach 07b608a778be6dac8669e04b9ab794c441959208

.venv-data/bin/python scripts/data/ssaju_policy_review.py build \
  --ssaju-root "$SSAJU_REVIEW_ROOT" \
  --prepare-external

.venv-data/bin/python scripts/data/ssaju_policy_review.py verify \
  --ssaju-root "$SSAJU_REVIEW_ROOT" \
  --report-dir data/reports/saju_1b_baseline/ssaju-policy-review/v1.0.0/review-c1e129b1e602
```

`build`는 external revision·focus file hash, canonical/staging/raw hash, 11K/5K 수량, external test/typecheck/build, 원국 진단, 73,049일 역변환을 fail-closed로 검사한다. 결과는 write-once review 경로에 저장하며 같은 identity에서 다른 byte가 나오면 덮어쓰지 않는다. `verify`는 review identity, artifact set, SHA-256 chain, 100건·지지별 25건 표본과 비변경 scope guard를 다시 확인한다.
