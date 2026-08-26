# Nemotron 사주 데이터 초기 다운로드 진행 기록

## 진행 기록

- 날짜: 2026-08-26
- 작업 요약: Hugging Face의 `rayraykim/Nemotron-Personas-Korea-Saju`에서 초기 분석용 데이터 카드와 첫 학습 Parquet shard를 고정 리비전으로 다운로드했다.
- 변경 범위: `data/nemotron_saju/`에 약 182MiB의 로컬 원본을 배치하고, 대용량 파일 Git 제외 규칙과 다운로드 명세를 추가했다.
- 검증 명령/결과:
  - `sha256sum data/nemotron_saju/data/train-00000-of-00003.parquet`: Hugging Face LFS OID와 일치했다.
  - DuckDB Parquet 검사: 66,666행, 40개 컬럼, UUID 66,666개로 중복이 없었다.
  - 핵심 필드 검사: `uuid`, `birth_datetime_synth`, `saju_pillars`, `saju_narrative` 누락 0건이었다.
  - JSON 검사: `saju_pillars`, `saju_sipsin`, `saju_narrative` 파싱 오류 0건이었다.
- 남은 이슈/후속 작업: 현재는 전체 1,000,000행 중 첫 shard만 확보했다. 내용 품질 분석과 한국식 만세력 재계산 결과를 바탕으로 추가 shard 다운로드 및 10K~20K 선별 여부를 결정한다.
