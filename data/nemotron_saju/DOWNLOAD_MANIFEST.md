# Nemotron 사주 데이터 다운로드 명세

## 원본

- 저장소: `rayraykim/Nemotron-Personas-Korea-Saju`
- 리비전: `ffb934248746a2dea64ef771c0d86e1743d25702`
- 라이선스: CC BY 4.0
- 다운로드 일자: 2026-08-26

## 현재 다운로드 범위

초기 조사 때 받은 v6·v7 각 1개 shard는 이력용 경로에 보존한다. Phase 1 정본은 같은 고정 revision의 v6 3개와 v7 17개 Parquet 전량이다.

| 항목 | 값 |
|---|---:|
| Parquet | 20개 |
| 전체 행 | 1,000,000 |
| v6 / v7 | 199,996 / 800,004 |
| 전체 bytes | 2,648,680,663 |
| UUID 중복 | 0 |
| row hash 중복 추정 | 0 |
| schema 일치 | 20/20 |

정본 원본은 다음 Git 제외 경로에 있다.

```text
data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702-full-1m/
```

이 경로의 `SOURCE_MANIFEST.json` SHA-256은 `df200cd8faf366bcc45d595b5d1c0a9f2ff422df5c33aaf2b5d71406938dc383`이다. 공개 가능한 전수 집계는 `data/reports/saju_1b_baseline/source/v1.1.0/build-9462ec148dcd/source_inventory.json`에 있으며 원문 행은 포함하지 않는다.

초기 2-shard 조사본은 아래 별도 경로에 남아 있고 Phase 1·2의 정본 입력으로 사용하지 않는다.

```text
data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702/
```

## 재현 명령

```bash
hf download rayraykim/Nemotron-Personas-Korea-Saju \
  --repo-type dataset \
  --revision ffb934248746a2dea64ef771c0d86e1743d25702 \
  --local-dir data/raw/nemotron_saju/ffb934248746a2dea64ef771c0d86e1743d25702-full-1m \
  --max-workers 2
```

다운로드 뒤에는 `SOURCE_MANIFEST.json`과 승인 source bundle을 다시 생성·검증해야 한다. 원본 파일은 크기가 크므로 Git 추적 대상에서 제외하며 기존 정본 snapshot을 덮어쓰지 않는다.
