# Nemotron 사주 데이터 다운로드 명세

## 원본

- 저장소: `rayraykim/Nemotron-Personas-Korea-Saju`
- 리비전: `ffb934248746a2dea64ef771c0d86e1743d25702`
- 라이선스: CC BY 4.0
- 다운로드 일자: 2026-08-26

## 현재 다운로드 범위

전체 저장소에는 학습 데이터 외 모델 어댑터와 평가 자료도 포함되어 있으므로, 초기 내용 분석용으로 데이터 카드와 첫 번째 학습 Parquet shard만 받았다.

| 파일 | 크기 | SHA-256 |
|---|---:|---|
| `README.md` | 33,122 bytes | Hugging Face 데이터 카드 |
| `data/train-00000-of-00003.parquet` | 190,451,215 bytes | `0ad75fedcd4df967592b510c9c31b3250123ef33933a281463323601506a1f22` |

Parquet 파일은 66,666행, 40개 컬럼을 포함한다. UUID 중복, 핵심 필드 누락, 주요 JSON 필드 파싱 오류는 초기 검사에서 발견되지 않았다.

## 재현 명령

```bash
hf download rayraykim/Nemotron-Personas-Korea-Saju \
  README.md data/train-00000-of-00003.parquet \
  --repo-type dataset \
  --revision ffb934248746a2dea64ef771c0d86e1743d25702 \
  --local-dir data/nemotron_saju \
  --max-workers 2
```

원본 파일은 크기가 크므로 Git 추적 대상에서 제외한다.
