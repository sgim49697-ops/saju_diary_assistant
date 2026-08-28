# 한국형 사주 1.3B Full Fine-tuning
## 초기 10K·20K 다중 소스 Baseline 데이터·학습 계획

작성 기준: 2026-08-27  
주 장비: NVIDIA GeForce RTX 5070 Ti 16GiB  
주 모델 후보: `kakaocorp/kanana-2-1.3b-base`  
학습 방식: BF16 전체 파라미터 Full Fine-tuning

---

## 0. 이번 초기 실험에서 고정할 전제

이번 실험은 **Nemotron 단독 실험이 아니다.** 처음부터 아래 다섯 종류를 섞어, 작은 모델이 사주 도메인 지식과 자연스러운 한국어 대화를 동시에 어느 정도 획득하는지 빠르게 확인한다.

| 데이터 축 | 초기 역할 |
|---|---|
| Nemotron 한국 사주 | 구조화된 원국·오행·십신을 읽고 한국어 풀이로 변환 |
| YEJI v9 Bazi | 상담형 질문, 장문 풀이, 다양한 운세 주제 |
| YEJI Processed Bazi | 여러 형태의 사주 Q&A와 질문 표현 다양성 |
| YEJI BaZi Translated KO | 간지·사주 4주 계산형 문항과 기본 용어 노출 |
| **한국어·공감 대화** | **사주 외 자연스러운 응답, 공감 표현, 멀티턴 대화 보존** |

초기 목표는 다음과 같다.

1. 사주 질문을 알아듣고 관련된 답을 생성한다.
2. 구조화된 원국 입력을 무시하지 않는다.
3. 성격·직업·재물·관계 등 서로 다른 질문 유형에 대응한다.
4. 딱딱한 사주 문구만 반복하지 않고 자연스러운 한국어로 반응한다.
5. 공감 대화에서 사주 이야기를 갑자기 꺼내거나, 사주 질문에 일반 상담 답만 하는 **태스크 혼동**을 줄인다.
6. 10K와 20K의 결과를 먼저 보고, 그다음 정제·한국식 보정·규칙 데이터로 모델을 천천히 깎는다.

초기 `v0 raw`에서는 다음을 하지 않는다.

- LLM으로 답변 전면 재작성
- 한국식 용어로 대규모 교정
- 전문가 수준의 용신·격국 재판정
- 의미 기반 근접 중복 제거
- 데이터 품질 점수에 따른 가중 학습
- 모델이 생성한 답을 다시 정답으로 사용

다만 학습 자체를 망가뜨리는 빈 값, 파싱 오류, 완전 중복, 심한 문자 깨짐은 제거한다.

---

# 1. 데이터 소스와 사용 목적

## 1.1 Nemotron-Personas-Korea-Saju

- 주소: https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju
- 라이선스: CC BY 4.0
- 공개 규모: 100만 행
- 주요 필드:
  - 합성 생년월일시 및 출생 지역
  - `saju_pillars`
  - `saju_day_master`
  - `saju_elements`
  - `saju_sipsin`
  - 4개 키의 `saju_narrative`
- 성격:
  - 사주 구조는 코드로 계산
  - 한국어 서사는 Qwen 계열 모델이 생성
  - 자동 validator로 구조·사실 언급·결정론 표현 등을 검사

### 초기 역할

`구조화된 사주 정보 → 한국어 풀이` 능력을 담당한다. 모델이 일간, 오행, 십신 등 입력 정보를 실제 답변에 반영하는지 확인하기 가장 좋은 소스다.

### 초기 주의점

- 전문가가 직접 작성한 통변 Gold는 아니다.
- 원국 계산 정책은 향후 한국형 엔진과 비교해야 한다.
- 페르소나 정보가 강해 모델이 이름·직업을 지나치게 반복할 수 있으므로 평가에서 별도로 확인한다.

---

## 1.2 YEJI Fortune-Telling KO v9 — Bazi subset

- 주소: https://huggingface.co/datasets/tellang/yeji-fortune-telling-ko-v9
- 전체 고유 예시: 31,625개
- 사주 Bazi: 8,423개
- 사주 데이터 출처 표기: `gpt5mini_synthesized`
- 라이선스: CC BY-NC 4.0

### 초기 역할

- 성향, 직업, 관계, 학업, 재물, 시기 질문 등 상담 주제 다양화
- Nemotron의 고정된 4키 JSON 스타일에만 모델이 묶이는 것을 방지
- 장문 한국어 사주 상담 문체 노출

### 중요한 로딩 규칙

이 데이터셋은 같은 31,625개를 Alpaca와 ChatML 두 형식으로 함께 제공한다. **둘을 동시에 로드하면 실질적으로 중복 학습**할 수 있으므로 첫 실험에서는 `train_alpaca.jsonl` 한 형식만 사용한다.

### 초기 주의점

- 비상업 라이선스이므로 Run 이름에 `NC`를 붙인다.
- 답변이 길어 토큰 비중을 과도하게 차지할 수 있으므로 길이 구간별로 균형 추출한다.
- 데이터 카드의 품질 주장은 제작자 자체 설명이므로, 전문가 Gold로 취급하지 않는다.

---

## 1.3 YEJI Processed — Bazi subset

- 주소: https://huggingface.co/datasets/tellang/yeji-processed
- 데이터 카드 주장:
  - 전체 43,704개
  - Bazi 21,798개
- 언어: 한국어·중국어
- 라이선스: MIT

### 초기 역할

- 질문 표현과 답변 형식의 다양성 확보
- 일반 Q&A형 사주 응답 학습
- Nemotron과 v9에서 부족한 질의 패턴 보완

### 초기 주의점

README 숫자와 실제 로더 결과가 항상 일치한다고 가정하지 않는다. 다운로드 직후 다음을 실제로 출력해 `inventory.json`에 저장한다.

```text
전체 행 수
split별 행 수
domain별 행 수
한국어/중국어 비율
입력·출력 평균 길이
exact duplicate 수
필드 누락 수
```

`domain` 문자열에 `bazi`가 포함된 행만 후보로 잡되, 완전히 중국어인 출력은 제외한다.

---

## 1.4 YEJI BaZi Translated KO

- 주소: https://huggingface.co/datasets/tellang/yeji-bazi-translated-ko
- 규모: 약 263K
- 언어: 중국어·한국어
- 라이선스: MIT
- 데이터 성격: 대부분 `생년월일시 → 사주 4주` 계산형 문항

### 초기 역할

- 갑자·을축 같은 간지 표기와 사주 4주 출력 형식 노출
- 짧고 정확한 계산형 응답 태스크 추가
- 상담형 데이터만 학습했을 때 생기는 과도한 장문 출력 완화

### 왜 5%만 넣는가

이 데이터는 대규모지만 대부분 해석문이 아니라 날짜에서 네 기둥을 출력하는 데이터다. 초기 모델의 목표는 사주 상담·해석이므로 비중을 높이면 모델이 모든 질문에 네 기둥만 짧게 출력하는 방향으로 끌릴 수 있다.

### 초기 주의점

- `question_ko`, `answer_ko`만 사용한다.
- `answer_ko`가 4개의 간지 쌍으로 구성된 행만 사용한다.
- `公历`, `左右`, 중국어 문장 잔재가 심한 행은 재작성하지 않고 제외한다.
- 이 단계의 정답은 한국식 만세력 기준으로 재검산하지 않았으므로 `hard_gt`가 아니라 `translated_weak_gt`로 표시한다.

---

## 1.5 한국어·공감 대화

초기부터 반드시 포함한다. 단순한 일반 instruction replay가 아니라, **사용자 말을 자연스럽게 받아주고, 감정을 과도하게 단정하지 않으며, 대화를 이어가는 능력**을 보존하는 역할이다.

### A. AI Hub 감성 대화 말뭉치

- 주소: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86
- 구축량: 코퍼스 약 27만 문장
- 특징:
  - 사용자 발화와 시스템 응답 대응
  - 60가지 세부 감정
  - 세대별 감성 대화

초기에는 `사용자 발화 → 시스템 응답`을 한 개의 SFT 예시로 사용한다.

### B. AI Hub 한국어 감정 정보가 포함된 연속적 대화

- 주소: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=271
- 구축량:
  - 연속 대화 10,000세트
  - 단발성 55,627문장
- 특징:
  - 평균 약 5.6개 발화의 멀티턴 대화
  - 행복·중립·슬픔·공포·혐오·분노·놀람 감정 레이블

초기에는 대화 세션을 나누지 않고, 앞의 1~4턴을 문맥으로 주고 다음 턴을 정답으로 만드는 방식으로 사용한다.

### AI Hub 사용 주의

AI Hub 원본 데이터는 이용 신청 및 이용정책을 따라야 한다. 원본 파일이나 변환된 원문을 공개 Git 저장소에 다시 올리지 않는다. 공개 저장소에는 변환 코드, 스키마, 해시, 통계만 올리고 실제 데이터 경로는 `.gitignore` 처리한다.

---

# 2. 확정 혼합 비율

## 2.1 핵심 비율

| 데이터 범주 | 비율 | 10K | 20K | 담당 능력 |
|---|---:|---:|---:|---|
| Nemotron Saju | 35% | 3,500 | 7,000 | 구조화 원국 기반 풀이 |
| YEJI v9 Bazi | 20% | 2,000 | 4,000 | 장문 상담·주제 다양성 |
| YEJI Processed Bazi | 25% | 2,500 | 5,000 | 다양한 사주 Q&A |
| YEJI Translated KO | 5% | 500 | 1,000 | 간지·4주 계산형 응답 |
| **한국어·공감 대화** | **15%** | **1,500** | **3,000** | 자연스러운 한국어·공감·멀티턴 |
| **합계** | **100%** | **10,000** | **20,000** | |

한국어·공감 대화 15%는 다음처럼 나눈다.

| 대화 소스 | 10K | 20K |
|---|---:|---:|
| AI Hub 감성 대화 말뭉치 | 1,000 | 2,000 |
| AI Hub 연속적 감정 대화 | 500 | 1,000 |
| 합계 | 1,500 | 3,000 |

## 2.2 20행 단위 균형 블록

혼합 순서가 한 소스에 몰리지 않도록 20행마다 다음 비율을 유지한다.

```text
Nemotron                 7
YEJI v9                  4
YEJI Processed           5
YEJI Translated          1
AI Hub 감성대화          2
AI Hub 연속대화          1
---------------------------
합계                     20
```

각 블록 내부는 무작위로 섞는다.  
500개 블록이 10K, 1,000개 블록이 20K다.

## 2.3 10K와 20K의 포함 관계

```text
MIX10-v0 ⊂ MIX20-v0
```

소스별 후보를 고정 seed로 한 번만 섞고, 각 소스의 앞 절반을 10K에 사용한다. 20K는 동일 후보의 범위를 두 배로 확장한다.

이렇게 해야 10K와 20K의 차이를 데이터 구성 변화가 아니라 **추가 데이터량 효과**로 해석할 수 있다.

---

# 3. 초기 Run 설계

## 3.1 Run 이름

| Run | 설명 |
|---|---|
| `K0-BASE` | Kanana 2 1.3B Base 원본 평가 |
| `K0-INSTRUCT` | Kanana 2 1.3B Instruct 원본 비교 평가 |
| `K1K-SMOKE-NC` | 혼합 1K 파이프라인·메모리 점검 |
| `K10-MIX-v0-RAW-NC` | 혼합 10K 최소 가공 Full FT |
| `K20-MIX-v0-RAW-NC` | 혼합 20K 최소 가공 Full FT |
| `K20-MIX-v1-LITE-NC` | 같은 20K ID를 가볍게 정제한 후속 비교용 |

`NC`는 YEJI v9의 CC BY-NC와 AI Hub 데이터 이용 조건이 포함된 연구·취미 브랜치임을 표시한다.

## 3.2 공식 비교는 독립 Run

정확한 비교를 위해 10K와 20K는 같은 Base checkpoint에서 각각 독립적으로 시작한다.

```text
Kanana Base ──> 10K 1 epoch
Kanana Base ──> 20K 1 epoch
```

빠른 중간 확인이 목적이라면 20K Run 도중 10K 노출 지점 checkpoint를 저장할 수 있다. 다만 이 checkpoint는 학습률 스케줄이 독립 10K Run과 다르므로 공식 비교 결과로는 사용하지 않는다.

---

# 4. 소스별 전처리 Adapter

모든 데이터를 한 함수에서 억지로 처리하지 않고, 소스별 adapter를 둔다.

```text
src/adapters/
├── nemotron_saju.py
├── yeji_v9.py
├── yeji_processed.py
├── yeji_translated.py
├── aihub_empathy.py
└── aihub_continuous_dialogue.py
```

각 adapter는 공통 스키마의 한 행을 반환한다.

## 4.1 Nemotron adapter

### 입력

다음 구조를 사람이 읽을 수 있는 고정 템플릿으로 직렬화한다.

```text
[태스크]
구조화된 사주 정보를 바탕으로 풀이하기

[사용자 배경]
연령: ...
성별: ...
직업: ...
지역: ...

[사주 구조]
연주: ...
월주: ...
일주: ...
시주: ...
일간: ...
오행 분포: ...
강한 오행: ...
부족 오행: ...
십신: ...

[요청]
주어진 정보만 근거로 사주 요약, 성향, 직업 흐름,
부족 오행 조언을 작성하세요.
```

### 정답

원본 `saju_narrative` 4키 JSON을 키 순서를 고정해 그대로 사용한다.

```json
{
  "saju_summary": "...",
  "personality_reading": "...",
  "career_reading": "...",
  "lacking_element_advice": "..."
}
```

### v0 필터

- `saju_narrative_error == null`
- 네 필드가 모두 존재
- 각 필드가 비어 있지 않음
- 입력 직렬화 실패가 없음

---

## 4.2 YEJI v9 adapter

### 입력

```text
[태스크]
사주 상담 질문에 답하기

{instruction}
{input이 있으면 추가}
```

### 정답

`output` 원문을 그대로 사용한다.

### v0 필터

- `domain == "bazi"`
- `instruction`, `output` 비어 있지 않음
- Alpaca 파일 한 개만 사용
- 전체 토큰이 최대 길이를 초과하면 해당 행을 버리고 같은 소스에서 보충

---

## 4.3 YEJI Processed adapter

### 입력

```text
[태스크]
사주 관련 질의에 답하기

{instruction}
```

### 정답

`output` 원문을 그대로 사용한다.

### v0 필터

- `domain`에 `bazi` 포함
- 입력·출력 존재
- 출력이 완전히 중국어인 행 제외
- 데이터 내부 exact duplicate 제거

---

## 4.4 YEJI Translated adapter

### 입력

```text
[태스크]
생년월일시에서 사주 네 기둥 출력하기

{question_ko}
```

### 정답

`answer_ko` 원문을 사용한다.

### v0 필터

- 답변이 정확히 네 개의 간지 토큰으로 구성
- 중국어 원문 필드는 학습 텍스트에 넣지 않음
- 한국어 질문에 중국어 잔재가 심한 행 제외
- 같은 생년월일시·같은 정답 중복 제거

---

## 4.5 AI Hub 감성대화 adapter

### 입력

```text
[태스크]
상대의 말을 자연스럽게 받아주며 공감적으로 답하기

{사용자 발화}
```

### 정답

AI Hub의 대응 시스템 응답을 사용한다.

### v0 필터

- 사용자 발화와 응답이 모두 존재
- 마스킹 문자만 있는 발화 제외
- 지나치게 짧은 기계적 응답 제외
- 개인정보처럼 보이는 전화번호·주민번호·이메일 패턴 제거 또는 행 제외

감정 라벨은 메타데이터에만 저장하고, v0에서는 프롬프트에 정답 감정을 직접 제공하지 않는다.

---

## 4.6 AI Hub 연속대화 adapter

한 세션의 발화를 다음처럼 변환한다.

```text
Turn 1: 사용자
Turn 2: 상대
Turn 3: 사용자
Turn 4: 상대
```

가능한 예시:

```text
입력: Turn 1
정답: Turn 2

입력: Turn 1 + Turn 2
정답: Turn 3

입력: Turn 1 + Turn 2 + Turn 3
정답: Turn 4
```

초기에는 한 세션에서 최대 2개 예시만 추출해 특정 대화가 과도하게 반복되지 않게 한다.

### v0 필터

- 세션 ID가 존재
- 2턴 이상
- 발화 순서 복원 가능
- 정답 발화가 비어 있지 않음
- train/eval 분할은 반드시 세션 ID 단위

---

# 5. 공통 데이터 스키마

```json
{
  "id": "source:original_id",
  "source": "nemotron_saju",
  "source_revision": "commit_or_version",
  "license": "CC-BY-4.0",
  "domain": "saju",
  "task": "structured_saju_reading",
  "messages": [
    {
      "role": "system",
      "content": "주어진 태스크와 입력에 맞게 한국어로 답하세요."
    },
    {
      "role": "user",
      "content": "..."
    },
    {
      "role": "assistant",
      "content": "..."
    }
  ],
  "label": {
    "stage": "v0_raw",
    "kind": "auto_validated_synthetic",
    "origin": "source_output",
    "human_review": "not_reviewed"
  },
  "quality_flags": {
    "parse_ok": true,
    "language_ok": true,
    "exact_duplicate": false,
    "translation_residue": false,
    "over_length": false
  },
  "meta": {
    "raw_hash": "sha256...",
    "conversation_id": null,
    "chart_signature": null,
    "input_tokens": 0,
    "assistant_tokens": 0,
    "total_tokens": 0
  }
}
```

## 태스크 값

```text
structured_saju_reading
open_saju_qa
pillar_calculation
empathic_response
natural_multiturn_dialogue
```

태스크 이름은 단순 메타데이터로만 남기지 않고, 초기에는 짧은 태스크 지시문을 실제 user/system 입력에 넣는다. 작은 모델이 서로 다른 출력 형태를 구분하는 데 필요하다.

---

# 6. v0 Raw에서 수행할 최소 전처리

## 6.1 반드시 수행

1. 원본 revision 또는 파일 버전 고정
2. 필드 누락·빈 문자열 제거
3. JSON/Parquet/Excel 파싱 실패 제거
4. Unicode NFC 정규화
5. 연속 공백과 비정상 제어문자 정리
6. source 내부 exact duplicate 제거
7. source 간 exact duplicate 제거
8. 완전히 중국어인 한국어 정답 제거
9. 최대 길이를 넘는 행은 중간 절단하지 않고 제외 후 보충
10. 모든 행에 source·task·license·raw hash 기록
11. 평가용 대화를 먼저 분리한 뒤 학습 manifest 생성
12. assistant 응답 토큰에만 loss가 걸리도록 경계 위치 저장

## 6.2 의도적으로 미룰 것

1. 번역체 문장 재작성
2. 용신·격국·신강신약 전문가 검증
3. 중국식 표현의 한국식 통일
4. 사주 원국 전체 재계산
5. 문장 의미 기반 dedup
6. LLM judge 품질 필터
7. 답변 길이 통일
8. 공감 대화의 스타일 재작성
9. 소스별 품질 가중치
10. 인간 Gold oversampling

## 6.3 길이 처리

첫 smoke test는 `max_seq_length=512`로 실행한다. 정상 동작 후:

```text
1순위: 1024 토큰
OOM 발생 시: 768 토큰
그래도 OOM이면: 512 토큰
```

답변을 문장 중간에서 잘라 정답으로 만들지 않는다. 최대 길이를 넘는 행은 제외하고 같은 소스·같은 길이 구간에서 다른 행으로 보충한다.

YEJI v9가 장문만 남거나 단문만 남지 않도록 source 내부를 답변 길이 사분위수로 나눠 동일한 비율로 샘플링한다.

## 6.4 토큰 비율 감사

행 비율만 보면 안 된다. manifest를 만든 뒤 다음 보고서를 저장한다.

```text
source별 행 수
source별 input token 수
source별 assistant token 수
평균·중앙값·p90·p95 길이
최대 길이 초과 제외율
태스크별 전체 loss token 비율
```

YEJI v9 20%가 assistant 토큰의 50% 이상을 차지하면, 텍스트를 다시 쓰지 않고 긴 구간의 샘플 수를 줄여 길이 분포만 재조정한다.

---

# 7. 데이터 누수 방지와 Split

## 7.1 평가셋을 먼저 고정

학습용 10K·20K를 뽑기 전에 아래 평가셋을 먼저 분리한다.

### Source holdout

| 소스 | 수량 |
|---|---:|
| Nemotron | 100 |
| YEJI v9 Bazi | 100 |
| YEJI Processed Bazi | 100 |
| YEJI Translated KO | 100 |
| AI Hub 감성 대화 | 100 |
| AI Hub 연속 대화 | 100 |
| 합계 | 600 |

### 직접 만든 Core Eval

총 200개를 별도로 만든다.

| 평가 영역 | 문항 수 |
|---|---:|
| 구조화된 원국 풀이 | 40 |
| 일반 사주 Q&A | 35 |
| 입력 사실 모순·환각 검사 | 35 |
| 간지·4주 계산 | 20 |
| 같은 명식 반복 질문 일관성 | 20 |
| 공감 응답 | 25 |
| 멀티턴 자연 대화 | 15 |
| 일반 한국어 instruction 보존 | 10 |
| 합계 | 200 |

## 7.2 그룹 분할 기준

| 데이터 | 그룹 ID |
|---|---|
| Nemotron | 원본 uuid + 사주 4주 signature |
| YEJI v9 | 정규화한 instruction hash·유사 템플릿 그룹 |
| YEJI Processed | 정규화 instruction hash |
| YEJI Translated | 생년월일시 + 네 기둥, 질문 템플릿 그룹 |
| AI Hub 감성대화 | 원본 대화/상황 ID |
| AI Hub 연속대화 | 전체 session ID |

같은 대화 세션의 일부 턴이 train과 eval 양쪽에 들어가면 안 된다.

---

# 8. 정답 라벨 설계

## 8.1 LLM SFT에서 정답 라벨의 의미

분류 모델처럼 `A/B/C` 클래스가 따로 있는 것이 아니다. Causal LM SFT에서는 **assistant가 출력해야 할 토큰 전체가 정답 라벨**이다.

```text
system + user 토큰: label = -100, loss 제외
assistant 토큰: 실제 token id, loss 적용
```

즉 `task`, `quality`, `source` 메타데이터를 저장하는 것과 실제 학습 정답은 별개다.

## 8.2 초기 소스별 라벨 지위

| 소스 | v0 정답 | 라벨 종류 | 의미 |
|---|---|---|---|
| Nemotron | 원본 4키 서사 JSON | `auto_validated_synthetic` | 구조 검사는 됐지만 전문가 통변 Gold는 아님 |
| YEJI v9 | 원본 output | `synthetic_soft_gt` | 생성형 상담 참고 정답 |
| YEJI Processed | 원본 output | `source_soft_gt` | 출처·품질 추가 감사 필요 |
| YEJI Translated | 원본 answer_ko | `translated_weak_gt` | 원천 계산 규칙 기준 정답, 한국식 검산 전 |
| AI Hub 감성대화 | 대응 시스템 응답 | `human_reference` | 자연스러운 참고 응답, 유일한 정답은 아님 |
| AI Hub 연속대화 | 실제 다음 발화 | `human_next_turn` | 실제 대화 다음 턴, 유일한 정답은 아님 |

초기 10K·20K에서는 이 라벨 종류를 **기록만 하고 가중치 차등은 주지 않는다.** 먼저 Raw 혼합 자체의 결과를 본다.

## 8.3 해석 정답과 계산 정답을 분리

### Hard label이 가능한 영역

- 연주·월주·일주·시주
- 일간
- 오행 수
- 일간 기준 십신
- 명시된 합·충 관계

이 항목은 나중에 한국형 계산 정책을 코드로 고정하면 `hard_gt_verified`로 승격할 수 있다.

### Soft label인 영역

- 성격 풀이
- 직업·관계 해석
- 오늘의 흐름
- 공감 답변
- 생활 조언

여기에는 유일한 정답문이 없다. 따라서 한 문장을 절대 정답으로 취급하지 않고 `reference answer`로 관리한다.

## 8.4 라벨 상태 값

```text
R0  source raw, 미검수
A1  자동 위생 검사 통과
A2  규칙·사실 자동 검산 통과
H1  사람이 확인하여 그대로 유지
H2  사람이 수정한 Gold
D   폐기
```

초기 10K·20K의 대부분은 `R0+A1`이다.

---

# 9. 10K 이후 정답 라벨 개선 절차

10K 모델 결과를 본 뒤 전체 데이터를 다시 라벨링하지 않는다. **오류가 많은 구간부터 소규모로 Gold를 만든다.**

## 9.1 첫 사람 검수 세트: 400개

| 추출 방식 | 수량 |
|---|---:|
| 소스별 무작위 샘플 | 180 |
| 10K 모델이 크게 틀린 예시 | 120 |
| 높은 loss 예시 | 50 |
| 소스 간 매우 비슷한 질문·상충 답변 | 50 |
| 합계 | 400 |

## 9.2 사람이 붙일 라벨

각 행에 다음을 기록한다.

```text
판정: KEEP / EDIT / DROP
입력 사실 반영: 0 / 1 / 2
사주 내부 논리: 0 / 1 / 2 / 판단불가
한국어 자연스러움: 1~5
질문 적합성: 1~5
공감·대화 자연스러움: 1~5
중국어·번역체 잔재: 없음 / 경미 / 심함
과도한 단정: 0 / 1
답변 반복·템플릿성: 0 / 1
학파·정책 의존: 없음 / 있음 / 불명
수정 정답: 선택 입력
검수 메모: 자유 입력
```

`EDIT`의 수정 답변만 `H2 Gold`가 된다. `KEEP`은 `H1`이고, `DROP`은 이후 manifest에서 제외한다.

## 9.3 모델 출력은 바로 정답으로 쓰지 않기

10K 모델이나 20K 모델이 생성한 답변은 다음 용도로만 사용한다.

- 오류 유형 수집
- hard sample mining
- 사람 검수 후보 선정
- 기존 정답과 비교

사람 또는 규칙 검증 없이 모델 출력을 다시 학습 정답으로 넣으면, 모델의 초기 오류를 반복 강화할 수 있다.

---

# 10. 20K 이후 라벨 개선 절차

20K 모델까지 비교한 뒤 다음 `K20-MIX-v1-LITE-NC`를 만든다. 기본 원칙은 **동일한 20K ID와 동일한 혼합 비율을 유지하고, 정답·필터 효과만 비교하는 것**이다. 다만 `DROP`이 필요한 행은 별도 replacement map을 남긴다. 결과 보고에서는 `(a) 같은 ID만 남긴 공통 subset`과 `(b) 같은 소스·태스크로 보충한 20K 전체`를 둘 다 평가한다.

## v1 Lite에 반영할 내용

1. `DROP` 판정 행은 제외하고, 보충할 경우 `old_id → replacement_id`를 기록
2. `H2` 수정 정답 적용
3. 명백한 중국어 잔재·번역 깨짐 제거
4. exact·near-exact template 중복 축소
5. 계산형 일부를 한국 기준 엔진으로 재검산
6. 입력에 없는 일간·십신을 언급하는 Nemotron/YEJI 행 표시 또는 제외
7. 공감 응답 중 훈계·진단·과도한 조언 행 제외
8. 태스크별 답변 길이 범위 조정

이 Run을 `K20-MIX-v0-RAW-NC`와 비교하면 **수량 증가 효과가 아니라 정제 효과**를 볼 수 있다.

---

# 11. Full Fine-tuning 초기 설정

## 11.1 주 모델

```text
kakaocorp/kanana-2-1.3b-base
```

카카오의 Kanana 2 1.3B는 3B 모델을 pruning·distillation한 경량 Base 모델이며, 한국어 토큰화와 온디바이스 배포를 목표로 공개된 계열이다.

`kanana-2-1.3b-instruct`는 학습하지 않은 비교군으로 함께 평가한다. Base가 10K에서 instruction 형식을 전혀 못 배우는 경우에만 동일 1K smoke 데이터를 Instruct에 학습해 데이터 문제와 Base 시작점 문제를 구분한다.

## 11.2 초기 설정

```yaml
model_name: kakaocorp/kanana-2-1.3b-base
full_finetune: true
precision: bf16
trust_remote_code: true
use_cache: false

max_seq_length: 1024       # OOM이면 768, 그다음 512
micro_batch_size: 1
gradient_accumulation_steps: 8

optimizer: paged_adamw_8bit
learning_rate: 8.0e-6
warmup_ratio: 0.03
lr_scheduler_type: cosine
weight_decay: 0.01
max_grad_norm: 1.0
num_train_epochs: 1

gradient_checkpointing: true
assistant_only_loss: true
packing: false
dynamic_padding: true
seed: 42
```

## 11.3 첫 smoke test

```text
데이터: 같은 비율의 혼합 1K
길이: 512
실행: 100~200 optimizer step
```

확인 항목:

- OOM 여부
- peak VRAM
- loss가 감소하는지
- assistant 토큰에만 loss가 걸리는지
- checkpoint 재로딩 가능 여부
- 생성이 빈 문자열·무한 반복으로 무너지지 않는지
- 각 태스크가 다른 출력 형태를 유지하는지

## 11.4 메모리 부족 시 순서

1. sequence 1024 → 768 → 512
2. gradient checkpointing 확인
3. `paged_adamw_8bit` 확인
4. 불필요한 evaluation batch 축소
5. DeepSpeed ZeRO-2 optimizer CPU offload 적용
6. 시스템 RAM이 충분하면 optimizer state를 CPU로 이동

첫 Run에서는 `torch.compile`, 강제 FlashAttention 교체, packing을 끈다. Kanana 2 1.3B는 custom hybrid attention 코드를 사용하므로 먼저 표준 학습 안정성을 확인한다.

---

# 12. 평가 지표

## 12.1 자동 평가

| 항목 | 측정 방식 |
|---|---|
| 정상 출력률 | 빈 출력·깨진 출력·무한 반복 여부 |
| 한국어 출력률 | 한국어/중국어/영어 문자 비율 |
| 태스크 혼동률 | 공감 질문에 사주 풀이, 계산 질문에 장문 상담 등 |
| JSON 파싱률 | Nemotron형 구조화 평가 |
| 입력 사실 위반률 | 입력에 없는 일간·천간·지지·십신 언급 |
| 계산 정확도 | 4주 계산 Exact Match, 단 한국식 검산 전에는 참고 지표 |
| 길이 준수 | 태스크별 예상 범위 |
| 반복률 | n-gram·문장 반복, 동일 답변 비율 |
| 일반 능력 보존 | 간단한 한국어 instruction 10~30문항 |

## 12.2 사람 평가

각 모델에서 동일 100문항을 블라인드 비교한다.

```text
K0-BASE
K0-INSTRUCT
K10-MIX-v0
K20-MIX-v0
```

평가 축:

- 질문에 맞는가
- 입력 정보를 실제로 활용하는가
- 사주 용어를 무작정 나열하지 않는가
- 자연스러운 한국어인가
- 공감이 과장되거나 훈계조가 아닌가
- 지나치게 뻔한 문장만 반복하지 않는가
- 같은 명식에서 앞뒤가 모순되지 않는가
- “재미로 다시 써볼 만하다”는 느낌이 있는가

## 12.3 초기 성공 기준 예시

다음은 절대적 진리 기준이 아니라 초기 개발 Gate다.

```text
정상 생성률              98% 이상
공감↔사주 태스크 혼동     5% 이하
Nemotron형 JSON 파싱률    90% 이상
명백한 입력 사실 위반     10% 이하
중국어 문장 혼입          3% 이하
K10 대비 K20 사람 선호    유의미한 우세
```

---

# 13. 결과에 따른 다음 조정

## 13.1 모델이 사주 답은 하지만 너무 딱딱하다

- 한국어·공감 대화 15% → 20%
- 연속 대화 문맥 길이 증가
- 짧은 공감형 사주 응답 Gold 추가

## 13.2 모든 질문에 위로만 하고 사주 근거가 약하다

- 공감 대화 15% → 10%
- Nemotron 35% → 40%
- 공감 데이터의 태스크 지시문을 더 명확히 분리
- 사주 평가에서 입력 사실 반영 항목 강화

## 13.3 모든 질문에 장문을 출력한다

- YEJI v9 token 비중 축소
- Translated 계산형 또는 짧은 사주 Q&A 비중 소폭 증가
- 태스크별 길이 지시 추가

## 13.4 네 기둥만 출력하고 풀이를 잘 못한다

- Translated 5% → 2% 또는 제거
- YEJI Processed/Nemotron 비중 증가

## 13.5 중국어·번역체가 많이 나온다

- YEJI Processed의 언어 감사 강화
- Translated 중국어 잔재 제거
- 20K v1에서 한국어 정규화 적용

## 13.6 10K와 20K 차이가 거의 없다

단순 수량 확장보다 데이터 내용 개선으로 이동한다.

- 상충 규칙 분리
- 계산 hard GT 생성
- 잘못된 해석 반례
- 전문가 또는 경험자 수정 Gold
- 한국식 서비스 출력 데이터

---

# 14. 권장 디렉터리 구조

```text
saju-1b/
├── data/
│   ├── raw/
│   │   ├── nemotron/
│   │   ├── yeji_v9/
│   │   ├── yeji_processed/
│   │   ├── yeji_translated/
│   │   ├── aihub_empathy/
│   │   └── aihub_continuous/
│   ├── unified/
│   │   ├── v0_raw/
│   │   └── v1_lite/
│   ├── manifests/
│   │   ├── mix1k_smoke_v0_nc.jsonl
│   │   ├── mix10k_v0_raw_nc.jsonl
│   │   ├── mix20k_v0_raw_nc.jsonl
│   │   └── mix20k_v1_lite_nc.jsonl
│   ├── eval/
│   │   ├── source_holdout_600.jsonl
│   │   ├── core_eval_200.jsonl
│   │   └── human_review_400.jsonl
│   └── reports/
│       ├── inventory.json
│       ├── token_stats_mix10.json
│       ├── token_stats_mix20.json
│       └── license_manifest.json
├── src/
│   ├── adapters/
│   ├── build_inventory.py
│   ├── build_unified.py
│   ├── build_manifests.py
│   ├── tokenize_stats.py
│   ├── train_fullft.py
│   ├── evaluate_auto.py
│   └── export_review_sheet.py
├── configs/
│   ├── smoke_1k.yaml
│   ├── fullft_10k.yaml
│   └── fullft_20k.yaml
└── runs/
    ├── K0-BASE/
    ├── K0-INSTRUCT/
    ├── K1K-SMOKE-NC/
    ├── K10-MIX-v0-RAW-NC/
    └── K20-MIX-v0-RAW-NC/
```

---

# 15. 실제 실행 순서

## Step 1. 원본 수집과 버전 고정

- 각 Hugging Face dataset revision 기록
- AI Hub 다운로드 버전·파일 목록 기록
- `license_manifest.json` 작성

## Step 2. Inventory

각 소스를 실제 로드해 행 수, 필드, 길이, 언어, 중복을 출력한다. README 숫자를 그대로 manifest 수량으로 사용하지 않는다.

## Step 3. 평가셋 우선 분리

- source holdout 600
- core eval 200
- session/group 단위 분리

## Step 4. Source adapter 작성

각 소스를 공통 `messages + label + meta` 스키마로 변환한다.

## Step 5. v0 최소 위생 필터

빈 값, 파싱 오류, exact duplicate, 심한 문자 깨짐, 길이 초과만 처리한다.

## Step 6. MIX20 manifest 먼저 생성

20행 균형 블록 1,000개를 만든다. 첫 500개 블록을 MIX10으로 고정한다.

## Step 7. 1K smoke

VRAM과 loss mask를 확인한다.

## Step 8. 10K Full FT

동일 Base에서 1 epoch 학습하고 고정 평가를 실시한다.

## Step 9. 오류 분류 및 400개 사람 검수

KEEP / EDIT / DROP 및 세부 품질 라벨을 작성한다.

## Step 10. 20K Full FT

동일 Base에서 독립적으로 1 epoch 학습한다.

## Step 11. 10K·20K 비교

양 증가 효과, 공감 대화 보존, 태스크 혼동, 사주 입력 근거성을 확인한다.

## Step 12. 다음 결정

- 20K가 확실히 좋아짐 → 50K 확장
- 수량 효과가 약함 → 같은 20K로 v1 Lite 정제 비교
- 자연스러움 부족 → 대화 비중 조정
- 사주 근거 부족 → 구조·규칙 데이터 강화

---

# 16. 가장 중요한 운영 원칙

1. **초기 10K·20K는 반드시 다중 소스 혼합으로 진행한다.**
2. **한국어·공감 대화 15%를 별도 축으로 유지한다.**
3. 10K는 20K의 정확한 부분집합으로 만든다.
4. 공식 10K·20K 모델은 같은 Base에서 독립 학습한다.
5. 원문을 크게 고치지 않되, 깨진 데이터까지 Raw라는 이유로 넣지 않는다.
6. 해석문은 절대 정답이 아니라 soft/reference label로 관리한다.
7. 계산 가능한 항목만 향후 hard GT로 승격한다.
8. 모델 출력은 검수 없이 학습 정답으로 재사용하지 않는다.
9. 평가셋은 학습 manifest보다 먼저 고정한다.
10. 행 수뿐 아니라 source별 assistant token 비중을 반드시 확인한다.
11. AI Hub 원문과 NC 데이터가 섞인 Run은 연구·취미 브랜치로 분리한다.
12. 10K·20K 결과가 나온 뒤에야 50K·100K 비율을 결정한다.

---

# 17. 참고 자료

- Kanana 2 1.3B Base: https://huggingface.co/kakaocorp/kanana-2-1.3b-base
- Kanana 2 1.3B Instruct: https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct
- Nemotron-Personas-Korea-Saju: https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju
- YEJI Fortune-Telling KO v9: https://huggingface.co/datasets/tellang/yeji-fortune-telling-ko-v9
- YEJI Processed: https://huggingface.co/datasets/tellang/yeji-processed
- YEJI BaZi Translated KO: https://huggingface.co/datasets/tellang/yeji-bazi-translated-ko
- AI Hub 감성 대화 말뭉치: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86
- AI Hub 한국어 감정 정보가 포함된 연속적 대화 데이터셋: https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=271
- AI Hub 데이터 이용정책: https://aihub.or.kr/intrcn/guid/usagepolicy.do
