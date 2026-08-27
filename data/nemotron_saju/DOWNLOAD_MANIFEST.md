# Nemotron 사주 데이터 다운로드 명세

## 원본

- 저장소: `rayraykim/Nemotron-Personas-Korea-Saju`
- 리비전: `ffb934248746a2dea64ef771c0d86e1743d25702`
- 라이선스: CC BY 4.0
- 다운로드 일자: 2026-08-26

## 현재 다운로드 범위

전체 저장소에는 학습 데이터 외 모델 어댑터와 평가 자료도 포함되어 있으므로, 초기 내용 분석용으로 데이터 카드와 v6·v7 학습 Parquet shard를 각각 하나씩 받았다.

| 파일 | 크기 | SHA-256 |
|---|---:|---|
| `README.md` | 33,122 bytes | Hugging Face 데이터 카드 |
| `data/train-00000-of-00003.parquet` | 190,451,215 bytes | `0ad75fedcd4df967592b510c9c31b3250123ef33933a281463323601506a1f22` |
| `data/train-extra-00000.parquet` | 129,879,649 bytes | `97616a1ae8725c68a665e9aef5396988cf16acfce1cc271a2c209c2b671d687a` |

두 Parquet 파일은 총 116,666행, 각각 40개 컬럼을 포함하며 스키마가 서로 일치한다. 파일 내부와 파일 간 UUID 중복, 핵심 필드 누락, 주요 JSON 필드 파싱 오류는 초기 검사에서 발견되지 않았다. v7 shard의 평균 사주 서사 길이는 552.5자로 v6 shard의 696.2자보다 짧아, 생성 버전별 문체·길이 차이를 후속 분석에서 분리해 다뤄야 한다.

Phase 1 실행으로 원본은 복사 없이 다음 Git 제외 경로로 이동했다.

`data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702/`

이동 후 두 Parquet의 bytes와 SHA-256을 다시 확인했으며 위 값과 일치했다. 원래 `data/nemotron_saju/`에는 공개 가능한 다운로드 명세만 남긴다.

## 재현 명령

```bash
hf download rayraykim/Nemotron-Personas-Korea-Saju \
  README.md \
  data/train-00000-of-00003.parquet \
  data/train-extra-00000.parquet \
  --repo-type dataset \
  --revision ffb934248746a2dea64ef771c0d86e1743d25702 \
  --local-dir data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702 \
  --max-workers 2
```

원본 파일은 크기가 크므로 Git 추적 대상에서 제외한다.
