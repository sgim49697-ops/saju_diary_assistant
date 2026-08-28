# MIX20K 외부 검수 소유자 평가

## 결론

- 제출본 `review-6e3ad5418434`은 패키지 `external-review-72fb212dc90369be`에 대한 자문 자료로 수용한다.
- 기술적 무결성과 여러 데이터 문제는 독립 재현했지만, 사람 명리 전문가 검수나 품질 인증으로 승격하지 않는다.
- 외부 권고는 canonical·Gate를 자동 변경하지 않으며 `advisory_only=true`로 유지한다.

## 독립 확인

- 공개 가능한 candidate 17,000건과 trainer projection 불일치: 0건
- bazi: 1,250개 고유 명식, 명식당 4행
- Nemotron target-only 전체 생년월일: 441행
- 반복 disclaimer: bazi 5,000행/20.28%, Nemotron 11,000행/10.68%
- YEJI: 고유 assistant 출력 221종, 조사 오류 102행
- 의미 표본 300건은 소스별 100건·token decile별 10건인 결정적 추출 결과와 순서까지 일치한다.

## 부분 재현·미확인

- target-only 이름은 제출 9,254행, 로컬 `NAME_PATTERN` 9,256행이다. 외부 matcher가 없어 2행 차이를 확정하지 않는다.
- 번역 잔재는 제출 775행, 로컬 한자 whitelist 787행이다. 외부 whitelist가 없어 정확한 집합을 확정하지 않는다.
- persona·직업·오행 보완 세부 건수는 외부 의미 패턴과 분석 코드가 없어 주장으로 보존한다.
- `ssaju` 십신 충돌 수치는 저장소에 runtime·버전·canonical policy가 없어 조건부 미확인으로 둔다.
- 대표 사례 참조 7개는 실제 candidate에 있으나 고유 ID는 4개이고 의미 표본 300건과 교집합이 없다.
- 행별 판정·메모, 외부 분석 코드, GPT 모델·버전, 실행 ID와 prompt hash가 없어 `semantic_reviewed_rows=300`은 제출자 자체 진술이다.

## AI Hub 경계와 후속 계약

- AI Hub 3,000건 본문 미제공은 누락이 아니라 승인되지 않은 제3자 공유를 막은 정상적인 정책 경계다.
- 공개 집계상 필터 통과 데이터는 53,768건·48,190개 대화 그룹이므로 로컬 증량 여력은 충분하다.
- 후속 `AIHUB-STYLE10K-v1`은 기존 MIX20K·평가군과 겹치지 않는 신규 그룹 10,000건(단일턴 5,000·멀티턴 5,000)으로 준비한다.
- 생성 전에 승인 범위 안에서 단일턴 100건·멀티턴 100건 이상을 로컬 검수한다. 원문·개별 ID·개별 hash는 Git이나 외부 검수 자료에 넣지 않는다.
- 이번 수용 작업은 계약만 기록하며 style 데이터 생성과 실제 학습은 수행하지 않는다.

## 유지하는 상태

- `training_promotion_allowed`: 기존 기술 상태 변경 없음
- `human_domain_review_performed=false`
- `quality_certification_claimed=false`
- `phase5_training_performed=false`
