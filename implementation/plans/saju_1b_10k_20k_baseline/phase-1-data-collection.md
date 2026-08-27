# Phase 1. 데이터 수집·버전 고정

| 항목 | 값 |
|---|---|
| 실행 상태 | 부분 진행 |
| 선행 Phase | Phase 0 완료 |
| 입력 | 승인된 실험 계약, 데이터 접근 권한, 저장 공간 |
| 출력 | `data/raw/`, `source_inventory.json`, `license_manifest.json` |
| 완료 Gate | 여섯 소스의 revision·파일·해시·이용조건 고정 |
| 웹 확인일 | 2026-08-27 |

## 목적

README의 수량을 신뢰해 바로 전처리하지 않고, 실제 원본 파일과 접근 조건을 재현 가능한 형태로 고정한다. 이 Phase에서는 원문을 수정하거나 학습 표본을 고르지 않는다.

## 소스 등록부

| 키 | 공식 저장소/페이지 | 고정 revision 또는 버전 | 라이선스 | 현재 상태 |
|---|---|---|---|---|
| `nemotron_saju` | `rayraykim/Nemotron-Personas-Korea-Saju` | `ffb934248746a2dea64ef771c0d86e1743d25702` | CC BY 4.0 | v6·v7 각 1 shard 확보 |
| `yeji_v9` | `tellang/yeji-fortune-telling-ko-v9` | `154f5582120e5c021c1fe1aa97c126785a1f32e7` | CC BY-NC 4.0 | 미수집 |
| `yeji_processed` | `tellang/yeji-processed` | `4fd7f404c80012aa9717368396131365e901b50c` | MIT | 미수집 |
| `yeji_translated` | `tellang/yeji-bazi-translated-ko` | `b494353378ea18a54f3502066e8075902049ec2f` | MIT | 미수집 |
| `aihub_empathy` | AI Hub `dataSetSn=86` | 승인 후 받은 배포 파일명·다운로드일 | AI Hub 이용정책 | 로컬 파일 미확인 |
| `aihub_continuous` | AI Hub `dataSetSn=271` | 승인 후 받은 배포 파일명·다운로드일 | AI Hub 이용정책 | 로컬 파일 미확인 |

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
  "license": "CC-BY-4.0",
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
- 학습 후보 7,000행보다 충분한 고유 행을 확보하고 v6 20%·v7 80% 계열을 구분해 inventory한다.

### YEJI v9

- `alpaca` config의 `train_alpaca.jsonl`만 수집한다.
- 동일 31,625개를 재표현한 `chatml` 파일을 동시에 학습 후보로 넣지 않는다.
- Bazi 8,423개라는 카드 설명은 inventory에서 실제 `domain` 값으로 다시 센다.

### YEJI Processed

- `train`과 `validation` Parquet을 모두 원본으로 보존하되 제작자 split을 학습/eval 계약으로 그대로 쓰지 않는다.
- README의 43,704/Bazi 21,798 수치는 참고값으로만 기록한다.
- `data/bazi_*.json`과 Parquet이 중복 원천인지 해시·ID·내용으로 inventory한 뒤 학습 후보 원천을 하나로 고정한다.

### YEJI Translated

- 단일 train Parquet을 고정 SHA로 수집한다.
- 중국어 원문 필드는 provenance로 보존하되 학습 텍스트에는 넣지 않는다.

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
- 라이선스와 출처 필드 존재 여부
- 파싱 성공률과 손상 파일 수

결과는 `data/reports/source_inventory.json`에 원본 수치와 필터 전 수치를 분리해 기록한다.

## 완료 Gate

- [ ] 여섯 소스가 모두 고정 revision/release 아래에 있다.
- [ ] 모든 파일의 bytes와 SHA-256이 manifest에 있다.
- [ ] YEJI v9는 Alpaca 한 형식만 학습 후보로 지정했다.
- [ ] YEJI Processed의 중복 원천 관계를 확인했다.
- [ ] AI Hub 승인과 비공개 저장 위치를 확인했다.
- [ ] 라이선스 manifest와 attribution 문구 초안을 작성했다.
- [ ] inventory가 README 주장과 실제 파일 수치를 분리해 기록한다.

Gate 실패 시 Phase 2 adapter 구현을 시작하지 않는다.

## 공식 자료

- [Hugging Face CLI 다운로드 가이드](https://huggingface.co/docs/huggingface_hub/en/guides/cli)
- [Nemotron-Personas-Korea-Saju](https://huggingface.co/datasets/rayraykim/Nemotron-Personas-Korea-Saju)
- [YEJI Fortune-Telling KO v9](https://huggingface.co/datasets/tellang/yeji-fortune-telling-ko-v9)
- [YEJI Processed](https://huggingface.co/datasets/tellang/yeji-processed)
- [YEJI BaZi Translated KO](https://huggingface.co/datasets/tellang/yeji-bazi-translated-ko)
- [AI Hub 감성 대화 말뭉치](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=86)
- [AI Hub 연속적 감정 대화](https://aihub.or.kr/aihubdata/data/view.do?dataSetSn=271)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Hugging Face API의 SHA·license·config | 등록부 값 확인, v9 Alpaca/ChatML 이중 제공 확인 |
| 2026-08-27 | AI Hub 데이터 페이지 | 로그인·신청 필요, 27만 코퍼스와 연속 대화 10,000세트 설명 확인 |
| 2026-08-27 | `hf download` 공식 사용법 | dataset type, revision, local-dir, dry-run 지원 확인 |
