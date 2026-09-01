# Phase 0. 거버넌스·실험 계약

| 항목 | 값 |
|---|---|
| 실행 상태 | 완료 |
| 선행 Phase | 없음 |
| 입력 | 본 정본, 모델·데이터 라이선스, 현재 장비 정보 |
| 출력 | 승인된 `experiment_contract.md`, 현재 `license-review-v1.0.0.json` |
| 완료 Gate | 실험 범위·배포 제한·버전 고정 정책 승인 |
| 웹 확인일 | 2026-08-28 |

## 목적

데이터를 추가로 받거나 모델을 내려받기 전에 실험 목적, 법적 범위, 비교 방법과 중단 규칙을 고정한다. 이 Phase의 승인은 기술적 성공과 별개로 모든 후속 작업의 필수 조건이다.

## 고정 범위

### 포함

- Kanana 2 1.3B Instruct의 1K smoke, 10K·20K BF16 Full FT
- Kanana Instruct 원본과 fine-tuned 모델 비교 평가
- 다섯 허용 데이터 축의 v1 혼합
- 고정 자동 기술평가와 K0·KI10·KI20 baseline 결정

### 제외

- 모델 접근 자체를 판매하는 API·클라우드 서비스, SI·on-premise·온디바이스 재판매
- LoRA/QLoRA, 지식 증류, 모델 병합
- 50K 이상 실제 학습
- AI Hub 원문 또는 변환 원문의 공개 저장소 게시
- 생년월일에서 네 기둥을 모델이 직접 계산하도록 학습하는 작업
- 고정 계산 계약으로 검산할 수 없는 해석을 사실상 확정적인 Gold로 승격하는 작업

## 기본 평가 권한

- 모든 Phase의 품질 Gate와 baseline 결정은 저장소 내부의 재현 가능한 자동 기술지표만 사용한다.
- 사람·독립 평가자·LLM 심사·외부 인증을 현재 또는 미래의 필수 조건으로 두지 않는다.
- 자동 계약으로 판정할 수 없는 해석 의미·취향·학파 의존 품질은 `not_measured`로 기록하고 해당 품질을 주장하지 않는다.
- `not_measured`는 Phase 완료나 기술 baseline 결정을 차단하지 않으며 별도 사용자 작업으로 전환하지 않는다.
- 모델 출력과 reference의 문자열 유사도는 품질 점수나 일반화 근거로 사용하지 않는다.

## 실험 ID와 비교 계약

| Run | 목적 | 시작 checkpoint |
|---|---|---|
| `K0-INSTRUCT` | Instruct 원본 비교 | Instruct 고정 revision |
| `KI1K-SMOKE-v1` | 파이프라인·메모리 검증 | Instruct 고정 revision |
| `KI10-MIX-v1` | 10K baseline | Instruct 고정 revision |
| `KI20-MIX-v1` | 20K baseline | Instruct 고정 revision |
| `KI20-MIX-v2-LITE` | 후속 정제 효과 비교 후보 | Instruct 고정 revision |

KI10과 KI20은 서로의 checkpoint를 이어받지 않는다. KI20 중간의 10K 노출 checkpoint는 관찰용일 뿐 공식 KI10 비교군으로 쓰지 않는다.

## 라이선스 Gate

### Kanana

공식 문서명 `KANANA OPEN LICENSE AGREEMENT`에서 fine-tuned 모델은 Derivative Works에 해당한다. 광고가 붙은 자체 앱은 상업적 사용으로 취급하되, Section 4.2의 자체 서비스 개발·운영 범위로 관리한다. `4.2`는 라이선스 버전이 아닌 조항 번호다. 공개 또는 배포 전에는 다음을 별도 검토한다.

- 라이선스 사본과 수정 사실 제공
- 요구되는 Notice 포함
- 관련 UI·문서에 `Powered by Kanana` 표시
- 다른 AI 모델로 배포할 때 이름에 `Kanana` prefix 적용
- API·클라우드 원격 접근 판매, SI/on-premise 판매, on-device 판매에 필요한 별도 상업 라이선스

모델 라이선스만 보면 이번 baseline은 광고형 자체 서비스 후보로 실행할 수 있다. 실제 사용은 아래 데이터 Gate까지 통과해야 하며, checkpoint 공개와 원격 모델 접근 판매는 Phase 6 이후 별도 승인 항목이다.

### 데이터

| 소스 | 라이선스/정책 | 운영 결론 |
|---|---|---|
| Nemotron Saju | CC BY 4.0 | revision·출처·변경 이력 기록 |
| `AmareshHebbar/bazi-sft` | Apache 2.0 | 구조 검산·한국어 재렌더 후 사용, 원문 응답 직접 학습 금지 |
| AI Hub 감성대화 #86 | AI Hub 일반정책·개별 신청 | AI 모델 학습 목적의 영리·비영리 연구개발에 한정. NIA·AI Hub 출처 표시, 미승인 제3자 열람·이전·판매 금지, 개인정보 발견 시 신고·해당 데이터 삭제. 한 원천에서 단일턴·멀티턴 축을 파생 |
| AI Hub 연속대화 #271 | KETI 데이터 정책 | 연구 목적 외 상업 이용은 별도 협의가 필요하므로 학습·다운로드 제외 |
| YEJI `shensha_51.json` | MIT + 원천 MIT | 단일 파일만 허용, 원천 코드 대조·오류 교정 후 자체 QA 생성 |
| YEJI v9·Processed·Translated·Interpretations | NC·원천 불명·무라이선스·품질 실패 | 활성 학습에서 제외, Translated만 비학습 감사 참고 허용 |

라이선스·원천·품질 Gate가 불명확하거나 사용 승인이 없는 소스는 혼합 비율을 자동 재배분하지 않고 Phase 1을 차단한다.

## 재현성과 보안 계약

- 모델·데이터는 branch 이름이 아니라 commit SHA로 고정한다.
- 다운로드 파일별 SHA-256, 크기, 원격 revision과 확인일을 기록한다.
- `HF_TOKEN`, AI Hub 인증정보, 서비스 키는 명령행 인자·로그·보고서에 출력하지 않는다. AI Hub 키는 Git 밖의 `~/.config/saju_diary_assistant/aihub.env`에서만 읽는다.
- 원본 데이터, checkpoint, optimizer state, 캐시는 Git에서 제외한다.
- 공개 Git에는 코드, 스키마, 해시, 집계 통계, 라이선스 manifest만 허용한다.
- 같은 Run 이름을 재사용하지 않는다. 재실행은 suffix와 부모 Run을 기록한다.

## 실행 절차

1. 모델과 각 데이터셋의 공식 라이선스 원문을 다시 연다.
2. 서비스 형태가 Kanana Section 4.1의 별도 상업 라이선스 대상인지 확인한다.
3. 고정 혼합비, Run 목록, 1K⊂10K⊂20K 계약을 승인한다.
4. 원본·checkpoint의 공개 금지 경로를 확인한다.
5. `experiment_contract.md`와 현재 `license-review-v1.0.0.json`에 승인·재검수 날짜를 기록한다.

## 완료 Gate

- [x] `KANANA OPEN LICENSE AGREEMENT`와 모든 데이터 이용조건을 확인했다.
- [x] 자체 서비스와 별도 상업 라이선스 대상 서비스의 경계를 승인했다.
- [x] 고정 Run·비율·비교 방법을 승인했다.
- [x] 모델·데이터 revision과 SHA-256 기록 형식을 승인했다.
- [x] 비밀값·원본·checkpoint Git 제외 정책을 확인했다.

하나라도 미완료면 Phase 1로 넘어가지 않는다.

## 공식 자료

- [Kanana 2 1.3B Instruct 모델 카드](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct)
- [Kanana Open License](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/blob/bf4786aa2a1908adce942d53976270132732f720/LICENSE)
- [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do)
- [AI Hub #271 데이터 페이지](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=271)
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- [`bazi-sft`](https://huggingface.co/datasets/AmareshHebbar/bazi-sft)
- [YEJI BaZi Rules](https://huggingface.co/datasets/tellang/yeji-bazi-rules)
- [현재 라이선스 재검토](../../../configs/data_versions/saju_1b_baseline/license-review-v1.0.0.json)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Kanana fine-tuning·배포·상업 조건 | 파생 저작물 의무와 특정 상업 형태의 별도 라이선스 필요 확인 |
| 2026-08-27 | AI Hub 이용정책 | 영리·비영리 연구개발, 학습 목적, 출처 표시, 신청·본인확인, 제3자 제공 금지 확인 |
| 2026-08-27 | 활성·제외 데이터 권리 사슬 | CC BY·Apache·MIT·AI Hub 허용 소스와 NC·무라이선스·원천 불명 소스 분리 |
| 2026-08-27 | AI Hub #271 제공 주체·정책 재검증 | KETI 데이터 분류와 별도 상업 협의 조건을 확인해 활성 소스에서 제외하고 #86 멀티턴 파생 축으로 대체 |
| 2026-08-28 | Kanana 고정 LICENSE·모델 카드 | 공식 문서명, Release Date, Section 3 배포 의무, Section 4.1·4.2 상업 경계를 재확인. 과거 `Kanana Open License 4.2` 표현은 현재 정오표로 대체 |
| 2026-08-28 | Nemotron·BaZi·YEJI 카드·원천 LICENSE | CC BY 4.0·Apache 2.0·MIT 표시와 품질 한계, YEJI 허용 규칙의 원천 MIT 계보를 재확인 |
| 2026-08-28 | AI Hub 현재 이용정책 | AI 학습 목적, NIA 출처, 미승인 제3자 열람·이전·판매 금지, 개인정보 발견 시 신고·해당 데이터 삭제 의무를 재확인 |
