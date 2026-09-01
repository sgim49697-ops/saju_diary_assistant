# experiment_contract.md - 사주 1.3B baseline의 승인된 Phase 0 실험 계약

# 사주 1.3B Baseline 실험 계약

| 항목 | 승인값 |
|---|---|
| 계약 버전 | `1.1.0` |
| 정본 문서 | `implementation/plans/saju_1b_10k_20k_baseline` v4.0.0 |
| 승인일 | 2026-08-27 |
| 재검수일 | 2026-08-28 |
| 승인 근거 | 사용자가 대화에서 구현과 Phase 0~1 진행을 명시적으로 승인 |
| 모델 | `kakaocorp/kanana-2-1.3b-instruct@bf4786aa2a1908adce942d53976270132732f720` |
| 학습 방식 | BF16 Full Fine-tuning, 1 epoch |
| 비교 Run | `K0-INSTRUCT`, `KI1K-SMOKE-v1`, `KI10-MIX-v1`, `KI20-MIX-v1` |

## 평가 권한

품질 Gate와 baseline 결정은 저장소 내부의 재현 가능한 자동 기술지표만 사용한다. 사람·독립 평가자·LLM 심사·외부 인증을 필수 조건이나 후속 작업으로 요구하지 않는다. 자동 계약으로 판정할 수 없는 의미 품질은 `not_measured`로 기록하고 주장하지 않으며, 이를 Phase 완료 blocker나 별도 사용자 작업으로 바꾸지 않는다.

Phase 6은 K0·KI10·KI20의 동일 설정 비교, sealed blind 500행 단회 실행, component→axis macro와 자동 no-regression으로 닫는다. 이 결과는 release·앱 연결·MIX20K-v3.1 생성·추가 학습을 승인하지 않는다.

## 서비스·배포 경계

광고가 일부 포함될 수 있는 자체 운영 서비스를 현재 허용 범위로 고정한다. API·클라우드 원격 모델 접근 판매, SI/on-premise 판매, on-device 판매, checkpoint 공개는 이 계약이 승인하지 않는다. 서비스 형태나 배포 방식이 바뀌면 모델과 모든 데이터 이용조건을 다시 검토한다.

모델 공식 문서명은 `KANANA OPEN LICENSE AGREEMENT`이며 `4.2`는 버전이 아닌 자체 서비스 경계 조항이다. 배포 시 라이선스 사본·수정 표시·Notice·`Powered by Kanana`를 제공하고, Kanana로 학습·향상한 모델 이름에 `Kanana` prefix를 적용한다. 자세한 현재 판정은 `configs/data_versions/saju_1b_baseline/license-review-v1.0.0.json`을 따른다.

이 문서는 기술적 사용 Gate이며 법률 자문을 대신하지 않는다.

## 데이터 원천과 혼합축

활성 원천은 네 개다.

| `source` | 원천 | 사용 Gate |
|---|---|---|
| `nemotron_saju` | Nemotron Saju | CC BY 4.0 표시·변경 기록 |
| `bazi_sft` | `AmareshHebbar/bazi-sft` | 구조 검산 후 자체 한국어 재렌더 |
| `aihub_empathy` | AI Hub 감성대화 #86 | 승인 계정·AI 학습 목적에서만 수집, NIA·AI Hub 출처 표시, 원문·미승인 제3자 열람 금지 |
| `yeji_bazi_rules` | YEJI `shensha_51.json` | MIT 원천 대조·오류 교정 후 자체 QA 생성 |

학습 할당은 아래 다섯 `mix_axis`로 고정한다.

| `mix_axis` | MIX1K | MIX10 | MIX20 |
|---|---:|---:|---:|
| `nemotron_saju` | 550 | 5,500 | 11,000 |
| `bazi_sft` | 250 | 2,500 | 5,000 |
| `aihub_empathy_single` | 100 | 1,000 | 2,000 |
| `aihub_empathy_multiturn` | 50 | 500 | 1,000 |
| `yeji_shensha_derived` | 50 | 500 | 1,000 |

`MIX1K-v1 ⊂ MIX10-v1 ⊂ MIX20-v1`이고, Nemotron 내부 비율은 v6 20%·v7 80%다. AI Hub #86 단일턴과 멀티턴 축의 group ID는 서로 겹치지 않게 분리한다.

AI Hub #271은 KETI 데이터 정책상 별도 상업 협의가 필요한 원천으로 판정했으므로 다운로드와 학습을 모두 금지한다. 그 5%를 #86의 구조적으로 적격한 멀티턴 group으로 대체한다.

## 보안·재현성 계약

- 원본, 변환 원문, 모델, checkpoint, optimizer state와 캐시는 Git에 올리지 않는다.
- AI Hub 키는 권한 `0600`인 `~/.config/saju_diary_assistant/aihub.env`의 `AIHUB_APIKEY`에서만 읽고 명령행·로그·보고서에 출력하지 않는다.
- AI Hub #86은 미승인 제3자에게 열람·제공·양도·대여·판매하지 않고, 개인정보 발견 시 AI Hub에 신고한 뒤 다운로드한 해당 데이터를 삭제한다.
- 원천 revision, 파일 크기와 SHA-256을 기록하며 branch나 `latest`를 사용하지 않는다.
- 공개 저장소에는 코드, 설정, 집계 통계, 해시와 라이선스 기록만 둔다.
- archive를 풀 때 절대경로, `..`, 심볼릭 링크, 하드 링크와 device member를 거부한다.

## 중단 조건

- 활성 원천의 권리·출처·승인 상태가 불명확하면 비율을 재배분하지 않고 해당 Phase를 차단한다.
- AI Hub #86에 reserve를 포함한 구조적 멀티턴 적격 group이 1,200개 미만이면 Phase 1을 차단한다.
- Phase 2 안전 필터 후 목표 수량을 채우지 못하면 자동 대체 없이 정본 계약을 다시 승인받는다.
- 모델이 생년월일로 사주 원국을 계산하는 데이터는 이 실험에 넣지 않는다.

## Phase 0 판정

- [x] 모델과 데이터 이용조건 검토
- [x] 자체 서비스와 별도 상업 계약 대상 경계 고정
- [x] Run·혼합비·부분집합 계약 고정
- [x] revision·SHA-256 기록 형식 고정
- [x] 비밀값·원본·checkpoint Git 제외 정책 고정

판정: **Phase 0 완료**. Phase 1은 각 원천의 실제 수집·해시·inventory Gate를 별도로 통과해야 한다.

2026-08-28 재검수에서 Phase 0 계약의 범위와 판정은 유지했고, 라이선스 문서명·AI Hub 통제 조건·정본 버전 표현만 교정했다.
