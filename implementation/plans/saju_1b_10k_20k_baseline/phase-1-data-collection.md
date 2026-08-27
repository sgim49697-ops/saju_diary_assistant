# Phase 1. 데이터 수집·버전 고정

| 항목 | 값 |
|---|---|
| 실행 상태 | 완료 |
| 선행 Phase | Phase 0 완료 |
| 입력 | 승인된 실험 계약, 데이터 접근 권한, 저장 공간 |
| 출력 | `data/raw/`, `source_inventory.json`, `license_manifest.json` |
| 완료 Gate | 네 활성 원천과 다섯 학습 축의 revision·파일·해시·이용조건 고정 |
| 웹 확인일 | 2026-08-27 |

## 목적

README의 수량을 신뢰해 바로 전처리하지 않고, 실제 원본 파일과 접근 조건을 재현 가능한 형태로 고정한다. 이 Phase에서는 원문을 수정하거나 학습 표본을 고르지 않는다.

## 소스 등록부

| 키 | 공식 저장소/페이지 | 고정 revision 또는 버전 | 라이선스 | 현재 상태 |
|---|---|---|---|---|
| `nemotron_saju` | `rayraykim/Nemotron-Personas-Korea-Saju` | `ffb934248746a2dea64ef771c0d86e1743d25702` | CC BY 4.0 | 20 Parquet·1,000,000행 전체 수집·해시 검증 완료 |
| `bazi_sft` | `AmareshHebbar/bazi-sft` | `fad87063b317612e4164dfb0e0e08572c3831df4` | Apache 2.0 | allowlist 6파일·100,000행 수집 완료 |
| `aihub_empathy` | AI Hub `dataSetSn=86` | file key `66046`~`66049` | AI Hub 이용정책 | 승인·수집·해시·구조 Gate 완료 |
| `yeji_bazi_rules` | `tellang/yeji-bazi-rules` | `84583ca54e8fce257d3d5efd015bca1263a1cfe9` | MIT + 원천 MIT | 허용 2파일·원천 3파일 수집 완료 |

고정 SHA는 2026-08-27 확인값이다. 실제 수집 시작 시 main SHA가 바뀌어도 자동으로 새 SHA를 택하지 않는다. 변경 내용을 검토해 정본 버전을 올린 뒤에만 교체한다.

## 현재 확보 데이터

분석용 두 shard는 과거 경로에 byte-for-byte 보존하고, 정본 전체 원천은 `data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702-full-1m/`에 별도 고정했다.

| 계열 | 파일 수 | 행 수 | 고유 명식 |
|---|---:|---:|---:|
| v6 | 3 Parquet | 199,996 | 136,996 |
| v7 | 17 Parquet | 800,004 | 255,719 |
| 합계 | 20 Parquet | 1,000,000 | 전역 266,950 |

전체 snapshot은 README·gitattributes를 포함해 22파일, 2,648,680,663 bytes이며 UUID 중복·빈 값·Parquet 행 중복은 0건이다. source bundle은 `v1.1.0/build-9462ec148dcd`로 고정했다.

`bazi-sft`는 고정 revision의 6파일 102,913,919 bytes와 100,000행을 수집했다. 원천 전체 exact row hash 중복은 0건이며, `synthetic_id` 25,000개가 각 4개 question type으로 구성돼 전체 ID 중복 수가 75,000건인 정상 구조임을 확인했다. 필수 컬럼 null은 0건이다. YEJI는 allowlist의 5파일 85,828 bytes만 수집했고 `shensha_51.json`의 고정 bytes·SHA-256이 일치했다. 상세 집계는 `data/reports/saju_1b_baseline/source/v1.1.0/build-9462ec148dcd/source_inventory.json`을 따른다.

AI Hub #86은 승인 계정에서 file key 4개를 수집했다. 원본 tar 합계는 21,350,912 bytes이고, tar·part·병합 zip 총 12파일을 SHA-256 manifest로 고정했다. 라벨 JSON 58,268건은 모두 파싱됐고 2개 이상 완전한 발화·응답 pair를 가졌다. 고유 `talk.id.talk-id` 51,886개로 최소 1,200-group Gate를 통과했다. upstream train/validation group 교집합은 6,379개지만 exact record 교집합은 0개다. Phase 2는 upstream split을 provenance로만 보존하고 전체 group을 다시 분리한다.

## 저장 규칙

이후 구현에서 모든 원본을 아래 계약으로 정리한다.

```text
data/raw/<source>/<revision-or-release>/
├── source files
├── SOURCE_MANIFEST.json
└── LICENSE-or-USAGE-NOTE.txt
```

기존 `data/nemotron_saju/` 파일은 Phase 1 실행 시 복사하지 않고 같은 파일시스템에서 `data/raw/nemotron_saju/<revision>/`으로 정리한다. 이동 전후 SHA-256이 같아야 한다.

`SOURCE_MANIFEST.json` 필수 필드는 다음과 같다.

```json
{
  "source": "nemotron_saju",
  "repo_or_provider": "rayraykim/Nemotron-Personas-Korea-Saju",
  "revision": "commit-or-provider-release",
  "retrieved_at": "ISO-8601",
  "license_expression": "CC-BY-4.0",
  "usage_class": "train_allow",
  "provenance_status": "verified",
  "attribution_ids": ["nvidia-nemotron-korea", "rayraykim-nemotron-saju"],
  "access_scope": "public-or-approved-account",
  "files": [
    {
      "path": "relative/path",
      "bytes": 0,
      "sha256": "..."
    }
  ]
}
```

## Hugging Face 수집 절차

모든 명령은 먼저 `--dry-run`으로 파일과 총용량을 확인한다. 토큰은 명령행에 직접 쓰지 않는다.

```bash
hf download <repo-id> \
  --repo-type dataset \
  --revision <commit-sha> \
  --dry-run

hf download <repo-id> <file-paths...> \
  --repo-type dataset \
  --revision <commit-sha> \
  --local-dir data/raw/<source>/<commit-sha>
```

### Nemotron

- 모델 adapter, survey 자료, 평가 결과는 baseline 원본 수집에서 제외한다.
- v6와 v7 서사는 생성 환경이 다르므로 `source_variant=v6|v7`을 manifest에 보존한다.
- 전체 20 Parquet를 구조·중복·문체 모집단으로 사용하고 `source_variant=v6|v7`을 파일 allowlist에서 고정한다.
- 학습 후보 11,000행보다 충분한 고유 행을 확보했으며 Phase 2 staging은 v6 2,640·v7 10,560을 선별한다.

### `bazi-sft`

- train·validation·test Parquet과 데이터 카드를 고정 revision으로 수집한다.
- 카드가 밝힌 100,000행, 4개 question type, 합성 인물, 자체 작성 규칙이라는 주장을 실제 파일에서 다시 센다.
- generator 저장소 링크가 placeholder이고 일·시주가 외부 검증되지 않았다는 한계를 manifest에 기록한다.
- 원본 English response를 직접 학습 후보로 지정하지 않는다. Phase 2가 구조화 사실·규칙 검산과 한국어 재렌더를 통과시킨 파생 행만 후보가 된다.

### YEJI 신살 규칙

- 저장소 전체를 받지 않고 `rules/shensha_51.json`과 README만 고정 revision으로 수집한다.
- `rules/shensha_51.json`의 bytes `29,754`, SHA-256 `9a11e1502983969407c43f82c65de6736b344da1a623e7a6557ad8b20cda939e`를 요구한다.
- 원천 `chxb/shensha@5b90110e55feb92303ef7853ecacdb6f9ed59eac`의 `LICENSE`, `README.md`, `shensha.js`를 provenance 자료로 별도 수집한다.
- `classics/*.txt`, `docs/sanming_tonghui_analysis.md`, `rules/yuanhai_ziping.json`은 `mymmsc/books`의 무라이선스 인터넷 수집본에서 파생됐으므로 다운로드·학습 후보에서 제외한다.
- `词馆` 규칙의 주석 `壬申`과 코드·JSON `壬卯` 상충을 known issue로 등록한다.

### 제외 소스 등록

다음 소스는 원본을 새로 다운로드하지 않는다. README 조사 결론과 고정 revision만 `license_manifest.json`의 `usage_class=deny|reference_only` 항목으로 보존한다.

| 소스 | usage class | 이유 |
|---|---|---|
| YEJI v9 | `deny` | CC BY-NC 4.0 |
| YEJI Processed | `deny` | 원천 권리 사슬 불명확·품질 실패 |
| YEJI Interpretations | `deny` | 명시 라이선스 없음 |
| YEJI Translated | `reference_only` | CC BY+MIT 의무, 해석 부재, 품질 감사 실패 |
| AI Hub #271 | `contract_required` | KETI 데이터로 분류되며 상업 이용은 별도 협의 대상 |

## AI Hub 수집 절차

AI Hub는 로그인·본인확인·사용 목적 제출과 데이터별 승인이 선행된다. 이 baseline은 일반정책 적용 대상인 #86만 수집하며, KETI 데이터인 #271에는 다운로드 요청을 보내지 않는다.

1. 사용자가 공식 페이지에서 #86을 신청하고 승인 상태를 확인한다.
2. API 키는 권한 `0600`인 `~/.config/saju_diary_assistant/aihub.env`의 `AIHUB_APIKEY`에서만 읽는다.
3. `66046`, `66047`, `66048`, `66049` 네 file key를 공식 API에서 받아 배포 원본과 승인일을 비공개 로컬 경로에 저장한다.
4. archive path traversal와 link/device member를 거부한 뒤 압축 전후 파일별 SHA-256과 크기를 기록한다.
5. 원문과 변환 원문은 Git, 공개 Hugging Face dataset, 외부 공유 폴더에 올리지 않는다.

로컬에서 승인 파일을 찾지 못하면 다음 메시지로 Phase를 차단한다.

```text
BLOCKED: AI Hub 데이터 #86의 승인과 비어 있지 않은 AIHUB_APIKEY가 필요합니다.
혼합 비율을 임의 재배분하지 않았습니다.
```

## 구현 명령

전용 환경은 `uv venv .venv-data`로 만들고 `uv pip install --python .venv-data/bin/python -r requirements-data.txt`로 준비한다.

```bash
.venv-data/bin/python scripts/data/phase1_sources.py validate-contract
.venv-data/bin/python scripts/data/phase1_sources.py download-hf --dry-run
.venv-data/bin/python scripts/data/phase1_sources.py download-hf --execute
.venv-data/bin/python scripts/data/phase1_sources.py download-aihub --dry-run
.venv-data/bin/python scripts/data/phase1_sources.py download-aihub --execute
.venv-data/bin/python scripts/data/phase1_sources.py inventory
.venv-data/bin/python scripts/data/phase1_sources.py verify --allow-missing-aihub
```

`--allow-missing-aihub`는 Phase 완료를 뜻하지 않는다. 공개 원천의 무결성만 검증하고 #86 미승인을 명시적인 차단 상태로 유지한다.

## Inventory 최소 항목

다운로드 직후 원문을 변경하지 않고 다음을 센다.

- 전체·파일·split·domain별 행 수
- 컬럼명, 자료형, 필드 누락 수
- exact duplicate와 ID duplicate 수
- 입력·출력 문자 길이와 파일 크기
- 한국어·중국어·영어 문자 비율
- 세션/그룹 ID 존재 여부
- 라이선스·usage class·출처·변환 사슬 필드 존재 여부
- 파싱 성공률과 손상 파일 수

결과는 `data/reports/saju_1b_baseline/source/v1.1.0/build-9462ec148dcd/source_inventory.json`에 원본 수치와 필터 전 수치를 분리해 기록한다.

## 완료 Gate

- [x] 네 활성 원천이 모두 고정 revision/release 아래에 있다.
- [x] #86 원천에서 단일턴·멀티턴 두 파생 축 계약에 필요한 구조가 확인됐다.
- [x] 모든 파일의 bytes와 SHA-256이 manifest에 있다.
- [x] `bazi-sft` 원본 응답은 직접 학습 후보가 아니며 파생 Gate를 명시했다.
- [x] YEJI Rules는 단일 허용 파일과 MIT 원천 코드만 수집했다.
- [x] 제외·참고 전용 소스를 활성 수집기가 읽지 못하게 했다.
- [x] AI Hub 승인과 비공개 저장 위치를 확인했다.
- [x] 라이선스 manifest와 attribution 문구 초안을 작성했다.
- [x] 전체 원천 inventory가 README 주장과 실제 파일 수치를 분리해 기록한다.

Gate 실패 시 Phase 2 adapter 구현을 시작하지 않는다.

## 공식 자료

- [Hugging Face CLI 다운로드 가이드](https://huggingface.co/docs/huggingface_hub/en/guides/cli)
- [Nemotron-Personas-Korea-Saju](https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju)
- [`bazi-sft`](https://huggingface.co/datasets/AmareshHebbar/bazi-sft)
- [YEJI BaZi Rules](https://huggingface.co/datasets/tellang/yeji-bazi-rules)
- [`chxb/shensha`](https://github.com/chxb/shensha)
- [AI Hub 감성 대화 말뭉치](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86)
- [AI Hub Shell 다운로드 안내](https://aihub.or.kr/devsport/apishell/list.do)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Hugging Face API의 SHA·license·files | 활성 소스 revision과 YEJI Rules 단일 허용 파일 확인 |
| 2026-08-27 | AI Hub #86 데이터 페이지·파일 API | 로그인·신청 필요, 네 배포 file key와 원천 내 연속 발화 구조 확인 |
| 2026-08-27 | AI Hub #271 데이터 페이지·정책 | KETI 데이터이므로 활성 수집에서 제외하고 별도 계약 전에는 요청하지 않도록 고정 |
| 2026-08-27 | `hf download` 공식 사용법 | dataset type, revision, local-dir, dry-run 지원 확인 |
| 2026-08-27 | `bazi-sft` 카드·샘플 | Apache 2.0, 합성·자체 규칙, 계산 검증 한계와 4개 question type 확인 |
| 2026-08-27 | YEJI Rules·원천 GitHub | 신살 JSON의 MIT 계보와 고전 파생 파일의 무라이선스 원천 확인 |
| 2026-08-27 | AI Hub 공식 Shell v0.6 | `AIHUB_APIKEY` 환경값, `/down/0.6/{dataset}.do?fileSn=...` 요청 형식과 tar·분할 zip 병합 방식을 확인하고 안전 추출기로 대체 구현 |
| 2026-08-27 | AI Hub #86 승인 수집·실파일 구조 | 네 file key의 tar·part·zip과 58,268 label record를 확인하고 51,886 고유 group으로 멀티턴 Gate 통과 |
| 2026-08-27 | Nemotron 고정 revision 전체 파일 API·실파일 | 20 Parquet 1,000,000행, v6 199,996·v7 800,004, UUID 중복 0으로 source bundle v1.1 승인 |
