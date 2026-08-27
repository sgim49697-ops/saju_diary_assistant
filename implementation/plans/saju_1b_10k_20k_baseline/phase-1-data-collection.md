# Phase 1. 데이터 수집·버전 고정

| 항목 | 값 |
|---|---|
| 실행 상태 | 부분 진행 |
| 선행 Phase | Phase 0 완료 |
| 입력 | 승인된 실험 계약, 데이터 접근 권한, 저장 공간 |
| 출력 | `data/raw/`, `source_inventory.json`, `license_manifest.json` |
| 완료 Gate | 다섯 활성 소스의 revision·파일·해시·이용조건 고정 |
| 웹 확인일 | 2026-08-27 |

## 목적

README의 수량을 신뢰해 바로 전처리하지 않고, 실제 원본 파일과 접근 조건을 재현 가능한 형태로 고정한다. 이 Phase에서는 원문을 수정하거나 학습 표본을 고르지 않는다.

## 소스 등록부

| 키 | 공식 저장소/페이지 | 고정 revision 또는 버전 | 라이선스 | 현재 상태 |
|---|---|---|---|---|
| `nemotron_saju` | `rayraykim/Nemotron-Personas-Korea-Saju` | `ffb934248746a2dea64ef771c0d86e1743d25702` | CC BY 4.0 | v6·v7 각 1 shard 확보 |
| `bazi_sft` | `AmareshHebbar/bazi-sft` | `fad87063b317612e4164dfb0e0e08572c3831df4` | Apache 2.0 | 미수집 |
| `aihub_empathy` | AI Hub `dataSetSn=86` | 승인 후 받은 배포 파일명·다운로드일 | AI Hub 이용정책 | 로컬 파일 미확인 |
| `aihub_continuous` | AI Hub `dataSetSn=271` | 승인 후 받은 배포 파일명·다운로드일 | AI Hub 이용정책 | 로컬 파일 미확인 |
| `yeji_shensha_derived` | `tellang/yeji-bazi-rules` | `84583ca54e8fce257d3d5efd015bca1263a1cfe9` | MIT + 원천 MIT | 미수집 |

고정 SHA는 2026-08-27 확인값이다. 실제 수집 시작 시 main SHA가 바뀌어도 자동으로 새 SHA를 택하지 않는다. 변경 내용을 검토해 정본 버전을 올린 뒤에만 교체한다.

## 현재 확보 데이터

`data/nemotron_saju/`에 다음 파일이 있다.

| 계열 | 파일 | 행 수 | 크기 | SHA-256 |
|---|---|---:|---:|---|
| v6 | `data/train-00000-of-00003.parquet` | 66,666 | 190,451,215 bytes | `0ad75fedcd4df967592b510c9c31b3250123ef33933a281463323601506a1f22` |
| v7 | `data/train-extra-00000.parquet` | 50,000 | 129,879,649 bytes | `97616a1ae8725c68a665e9aef5396988cf16acfce1cc271a2c209c2b671d687a` |

두 파일은 분석용 부분 확보분이다. 전체 1M 모집단으로 오인하지 않으며, 수집 완료 여부와 학습 후보 수량은 별개로 관리한다.

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
- 현재 두 shard는 구조·문체 비교에는 쓰되 전체 분포 추정의 유일한 근거로 삼지 않는다.
- 학습 후보 11,000행보다 충분한 고유 행을 확보하고 v6 20%·v7 80% 계열을 구분해 inventory한다.

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

## AI Hub 수집 절차

AI Hub는 로그인·본인확인·사용 목적 제출과 데이터별 승인이 선행된다.

1. 사용자가 공식 페이지에서 두 데이터셋을 각각 신청한다.
2. 승인 완료 후 공식 Shell/API 다운로드 방식과 제공 설명서를 확인한다.
3. 배포 압축 파일 원본, 파일 목록, 설명서, 승인일을 비공개 로컬 경로에 저장한다.
4. 압축 해제 전후 파일별 SHA-256과 크기를 기록한다.
5. 원문과 변환 원문은 Git, 공개 Hugging Face dataset, 외부 공유 폴더에 올리지 않는다.

로컬에서 승인 파일을 찾지 못하면 다음 메시지로 Phase를 차단한다.

```text
BLOCKED: AI Hub 데이터 86/271의 승인된 원본 경로가 필요합니다.
혼합 비율을 임의 재배분하지 않았습니다.
```

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

결과는 `data/reports/source_inventory.json`에 원본 수치와 필터 전 수치를 분리해 기록한다.

## 완료 Gate

- [ ] 다섯 활성 소스가 모두 고정 revision/release 아래에 있다.
- [ ] 모든 파일의 bytes와 SHA-256이 manifest에 있다.
- [ ] `bazi-sft` 원본 응답은 직접 학습 후보가 아니며 파생 Gate를 명시했다.
- [ ] YEJI Rules는 단일 허용 파일과 MIT 원천 코드만 수집했다.
- [ ] 제외·참고 전용 소스를 활성 adapter가 읽지 못하게 했다.
- [ ] AI Hub 승인과 비공개 저장 위치를 확인했다.
- [ ] 라이선스 manifest와 attribution 문구 초안을 작성했다.
- [ ] inventory가 README 주장과 실제 파일 수치를 분리해 기록한다.

Gate 실패 시 Phase 2 adapter 구현을 시작하지 않는다.

## 공식 자료

- [Hugging Face CLI 다운로드 가이드](https://huggingface.co/docs/huggingface_hub/en/guides/cli)
- [Nemotron-Personas-Korea-Saju](https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju)
- [`bazi-sft`](https://huggingface.co/datasets/AmareshHebbar/bazi-sft)
- [YEJI BaZi Rules](https://huggingface.co/datasets/tellang/yeji-bazi-rules)
- [`chxb/shensha`](https://github.com/chxb/shensha)
- [AI Hub 감성 대화 말뭉치](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86)
- [AI Hub 연속적 감정 대화](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=271)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Hugging Face API의 SHA·license·files | 활성 소스 revision과 YEJI Rules 단일 허용 파일 확인 |
| 2026-08-27 | AI Hub 데이터 페이지 | 로그인·신청 필요, 27만 코퍼스와 연속 대화 10,000세트 설명 확인 |
| 2026-08-27 | `hf download` 공식 사용법 | dataset type, revision, local-dir, dry-run 지원 확인 |
| 2026-08-27 | `bazi-sft` 카드·샘플 | Apache 2.0, 합성·자체 규칙, 계산 검증 한계와 4개 question type 확인 |
| 2026-08-27 | YEJI Rules·원천 GitHub | 신살 JSON의 MIT 계보와 고전 파생 파일의 무라이선스 원천 확인 |
