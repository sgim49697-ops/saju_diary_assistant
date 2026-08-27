# language-bank-prompt-v1.0.0.md - 한국어 고정 문구 은행 초안 생성 요청

당신은 한국어 데이터 편집자다. 파일을 수정하지 말고 JSON만 반환한다.

읽을 수 있는 고정 원천은 다음 두 파일뿐이다.

- `data/raw/bazi_sft/fad87063b317612e4164dfb0e0e08572c3831df4/README.md`
- `data/raw/yeji_bazi_rules/84583ca54e8fce257d3d5efd015bca1263a1cfe9/rules/shensha_51.json`

목표는 원문 100,000행을 번역하는 것이 아니라 작은 한국어 문구 은행 하나를 만드는 것이다.

## BaZi 문구

- question type `career`, `element_balance`, `general_natal`, `relationships` 각각에 자연스러운 한국어 질문을 하나 작성한다.
- rule ID `day_master_strong`, `day_master_weak`, `dm_supported`, `dominant_element`, `missing_elements` 각각에 1문장 설명을 작성한다.
- 규칙 설명은 입력에서 따로 제시할 일간·오행 수치를 되풀이하지 말고 조건의 의미만 설명한다.
- 단정, 예언, 건강 진단, 투자 조언, 성공·관계 보장을 금지한다.

## YEJI 문구

- `shensha_51.json`의 51개 규칙을 ID 순으로 정확히 한 번씩 반환한다.
- `name_ko`는 원문과 글자 단위로 같아야 한다.
- `safe_meaning_ko`는 원문의 `meaning`에 이미 있는 의미만 1문장으로 중립화한다.
- "전통 명리에서는 ~을 상징하는 참고 요소로 본다"처럼 문화·오락적 참고임을 드러낸다.
- 죽음, 질병, 재난, 이혼, 불임, 수명, 성공, 부, 시험 합격 등을 확정하지 않는다.
- 원문에 없는 효능·인과·조언을 추가하지 않는다.
- 중국어 문장이나 병음은 쓰지 않는다. 규칙명에 필요한 한자 표기는 출력하지 않는다.

출력은 제공된 JSON Schema를 정확히 만족해야 하며 Markdown이나 설명문을 덧붙이지 않는다.
