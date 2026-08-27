# Phase 0. 거버넌스·실험 계약

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | 없음 |
| 입력 | 본 정본, 모델·데이터 라이선스, 현재 장비 정보 |
| 출력 | 승인된 `experiment_contract.md`, `license_manifest.json` 초안 |
| 완료 Gate | 실험 범위·배포 제한·버전 고정 정책 승인 |
| 웹 확인일 | 2026-08-27 |

## 목적

데이터를 추가로 받거나 모델을 내려받기 전에 실험 목적, 법적 범위, 비교 방법과 중단 규칙을 고정한다. 이 Phase의 승인은 기술적 성공과 별개로 모든 후속 작업의 필수 조건이다.

## 고정 범위

### 포함

- Kanana 2 1.3B Base의 1K smoke, 10K·20K BF16 Full FT
- Kanana Base와 Instruct 원본 비교 평가
- 다섯 데이터 축의 v0 Raw 혼합
- 고정 평가, 400건 사람 검수, v1 Lite 또는 50K 진입 결정

### 제외

- 상업 API·유료 서비스·SI·온디바이스 재판매
- LoRA/QLoRA, 지식 증류, 모델 병합
- 50K 이상 실제 학습
- AI Hub·NC 원문 또는 변환 원문의 공개 저장소 게시
- 전문가 검수 없이 사주 해석을 사실상 확정적인 Gold로 승격하는 작업

## 실험 ID와 비교 계약

| Run | 목적 | 시작 checkpoint |
|---|---|---|
| `K0-BASE` | Base 원본 성능 | Base 고정 revision |
| `K0-INSTRUCT` | Instruct 원본 비교 | Instruct 고정 revision |
| `K1K-SMOKE-NC` | 파이프라인·메모리 검증 | Base 고정 revision |
| `K10-MIX-v0-RAW-NC` | 10K baseline | Base 고정 revision |
| `K20-MIX-v0-RAW-NC` | 20K baseline | Base 고정 revision |
| `K20-MIX-v1-LITE-NC` | 후속 정제 효과 비교 후보 | Base 고정 revision |

K10과 K20은 서로의 checkpoint를 이어받지 않는다. K20 중간의 10K 노출 checkpoint는 관찰용일 뿐 공식 K10 비교군으로 쓰지 않는다.

## 라이선스 Gate

### Kanana

KananaOpenLicense에서 fine-tuned 모델은 Derivative Works에 해당한다. 공개 또는 배포 전에는 다음을 별도 검토한다.

- 라이선스 사본과 수정 사실 제공
- 요구되는 Notice 포함
- 관련 UI·문서에 `Powered by Kanana` 표시
- 다른 AI 모델로 배포할 때 이름에 `Kanana` prefix 적용 여부
- API·클라우드 원격 접근 판매, SI/on-premise 판매, on-device 판매에 필요한 별도 상업 라이선스

이번 baseline은 비상업 연구·취미 범위에서만 실행한다. 외부 배포 결정은 Phase 6 이후 별도 승인 항목이다.

### 데이터

| 소스 | 라이선스/정책 | 운영 결론 |
|---|---|---|
| Nemotron Saju | CC BY 4.0 | revision·출처·변경 이력 기록 |
| YEJI v9 Bazi | CC BY-NC 4.0 | 전체 Run을 `NC`로 표시, 상업 사용 금지 |
| YEJI Processed | MIT | license 사본·revision 기록 |
| YEJI Translated | MIT | license 사본·revision 기록 |
| AI Hub 2종 | AI Hub 이용정책·개별 신청 | 학습 목적만 사용, 제3자 제공·원문 공개 금지, 사업결과 출처 표시 |

라이선스가 불명확하거나 사용 승인이 없는 소스는 혼합 비율을 자동 재배분하지 않고 Phase 1을 차단한다.

## 재현성과 보안 계약

- 모델·데이터는 branch 이름이 아니라 commit SHA로 고정한다.
- 다운로드 파일별 SHA-256, 크기, 원격 revision과 확인일을 기록한다.
- `HF_TOKEN`, AI Hub 인증정보, 서비스 키는 환경변수·로그에 출력하지 않는다.
- 원본 데이터, checkpoint, optimizer state, 캐시는 Git에서 제외한다.
- 공개 Git에는 코드, 스키마, 해시, 집계 통계, 라이선스 manifest만 허용한다.
- 같은 Run 이름을 재사용하지 않는다. 재실행은 suffix와 부모 Run을 기록한다.

## 실행 절차

1. 모델과 각 데이터셋의 공식 라이선스 원문을 다시 연다.
2. 실험 목적이 비상업 연구·취미 범위를 벗어나지 않는지 확인한다.
3. 고정 혼합비, Run 목록, 10K⊂20K 계약을 승인한다.
4. 원본·checkpoint의 공개 금지 경로를 확인한다.
5. `experiment_contract.md`와 `license_manifest.json` 초안에 승인자·날짜를 기록한다.

## 완료 Gate

- [ ] KananaOpenLicense와 모든 데이터 이용조건을 확인했다.
- [ ] `NC` 범위와 외부 배포 금지를 승인했다.
- [ ] 고정 Run·비율·비교 방법을 승인했다.
- [ ] 모델·데이터 revision과 SHA-256 기록 형식을 승인했다.
- [ ] 비밀값·원본·checkpoint Git 제외 정책을 확인했다.

하나라도 미완료면 Phase 1로 넘어가지 않는다.

## 공식 자료

- [Kanana 2 1.3B Base 모델 카드](https://huggingface.co/kakaocorp/kanana-2-1.3b-base)
- [Kanana Open License](https://huggingface.co/kakaocorp/kanana-2-1.3b-base/blob/e9ffedf7b713530ae6a0c94ea32538d75e8524e1/LICENSE)
- [AI Hub 데이터 이용정책](https://aihub.or.kr/intrcn/guid/usagepolicy.do)
- [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
- [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Kanana fine-tuning·배포·상업 조건 | 파생 저작물 의무와 특정 상업 형태의 별도 라이선스 필요 확인 |
| 2026-08-27 | AI Hub 이용정책 | 학습 목적, 출처 표시, 신청·본인확인, 제3자 제공 금지 확인 |
