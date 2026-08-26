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

## 원격 저장소 게시

- 날짜: 2026-08-26
- 공개 저장소: `https://github.com/sgim49697-ops/saju_diary_assistant`
- 원격 설정: `origin`을 추가하고 로컬 `master`를 `origin/master`에 push했다.
- 검증 결과: GitHub 저장소의 `isPrivate=false`, 기본 브랜치 `master`, 로컬 브랜치의 upstream 연결을 확인했다.

## 초기 shard 대표성 판단

- 현재 받은 66,666행은 전체 1,000,000행의 약 6.7%이며, 기존 v6 데이터 199,996행을 구성하는 3개 shard 중 첫 파일이다.
- 최신 v7 추가 데이터 800,004행은 별도의 17개 shard와 다른 생성 환경(`Qwen3-30B-A3B-FP8`, vLLM)으로 만들어졌으므로 현재 파일만으로 서사 품질과 문체를 대표할 수 없다.
- 현재 파일은 스키마, 결정론적 사주 필드, 기본 결측·중복 검사에는 적합하다.
- 전체 내용 분석에는 v6 20%와 v7 80% 비율을 유지해 각 shard에서 추출한 약 10K의 층화 표본을 별도로 만드는 것을 권장한다.
