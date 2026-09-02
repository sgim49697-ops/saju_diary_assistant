<!-- chart_only_operations.md - v1.4 chart-only runtime과 dashboard v1.9 production binding의 운영 경계를 기록한다. -->

# chart-only 운영 계약

이 문서는 `saju-runtime-release-v1.4.0-63dc8d398e90`을 dashboard `v1.9.0`에 제한 결합하는 절차를 설명한다. 앱은 로그인이나 API key 없이 공개할 수 있지만 runtime HMAC key와 session AEAD key는 공개 자격 증명이 아니다. 두 key의 값·hash·실제 경로는 브라우저, 모델 process, 로그, Git, 공개 보고서에 노출하지 않는다.

production binding 구현 자체는 완료됐지만 runtime feature의 설정 기본값은 계속 off다. 합성 HTTP 100건과 실제 K0·KI20 GPU 1쌍을 모두 통과한 process에서만 `--enable-chart-only-runtime`을 명시해 제한 활성화한다.

v1.9 실행 진입점은 `scripts/training/phase5_dashboard_v1_9.py`다. `scripts/training/phase5_dashboard.py`는 과거 후보 Gate가 byte hash로 고정한 v1.8 진입점이므로 수정하거나 v1.9 실행에 재사용하지 않는다.

## 고정 보안·운영 경계

- runtime HMAC key와 session encryption key는 서로 다른 현재 사용자 소유 0600 단일-link 일반 파일이어야 한다. 두 key는 각각 32바이트다.
- HMAC은 opaque runtime capability 생성용이며 사용자 인증이나 출생 state 암호화를 대신하지 않는다. capability는 브라우저 메모리에만 두고 모델 subprocess에는 전달하지 않는다.
- session state는 AES-256-GCM, write마다 새 12바이트 OS 난수 nonce, `schema_version+session_id+key_id` associated data로 암호화한다.
- 저장 root는 0700, record는 0600이며 plaintext·envelope 상한, 최대 100 session, 마지막 write부터 1,800초 보존과 명시적 삭제를 적용한다.
- 한 process만 persistence lease를 소유한다. 다중 HTTP thread의 runtime 계산은 직렬화하며 이미 계산 중이면 `429`와 `Retry-After`를 반환한다.
- 전역 분당 제한은 session/chart 30, runtime event 300, 모델 생성 10이다. stale revision은 `409`, v1.8 legacy runtime API는 `410`으로 닫는다.
- 공개 무인증 모드에서도 exact Host·Origin과 CSRF token을 모두 검증한다. 무인증 공개는 URL 접근자가 임의 입력과 GPU 생성 기능을 사용할 수 있다는 뜻이며 secret 공개를 뜻하지 않는다.
- 운영 로그에는 HTTP method, 정규화한 route template, status, reason만 남긴다. request body, 출생정보, runtime capability, ciphertext, nonce는 남기지 않는다.
- HMAC key rotation은 기존 session capability를 무효화하고 재계산한다. encryption key rotation은 최대 이전 key 1개로 기존 record를 읽은 직후 active key로 재암호화한다.
- key 삭제는 남은 ciphertext에 대한 cryptographic erasure로만 기록한다. SSD·filesystem의 물리적 secure overwrite를 주장하지 않는다.

## 공개 API와 모델 binding

dashboard `v1.9.0`의 runtime API는 아래 네 route만 사용한다.

```text
GET    /api/runtime/status
POST   /api/runtime/sessions
POST   /api/runtime/sessions/{session_id}/events
DELETE /api/runtime/sessions/{session_id}
```

입력은 동의 후 태양력/음력, 날짜, 정확/범위/미상 시각, 대한민국 도시만 구조화 event로 받는다. 자유문 runtime parsing, 좌표·성별 입력과 기간 계산은 허용하지 않는다. 공개 snapshot은 승인된 원국 사실 allowlist와 SHA-256만 포함한다. K0와 KI20에는 이 동일 canonical snapshot을 각각의 분리된 대화 context에 결합하며 원시 runtime capability는 전달하지 않는다.

## 설치·계약 확인

기존 GPU dashboard 환경에는 PyTorch를 다시 설치하지 않고 adapter 의존성만 추가한다.

```bash
uv pip install --python .venv/bin/python \
  -r requirements-runtime-adapter-v1.0.txt
uv pip check --python .venv/bin/python

.venv/bin/python -m scripts.runtime.chart_only_operations validate-contract
.venv/bin/python -m scripts.runtime.chart_only_operations environment
.venv/bin/python -m scripts.runtime.chart_only_dashboard_operations validate-contract
.venv/bin/python -m scripts.runtime.chart_only_dashboard_operations environment
.venv/bin/python -m scripts.runtime.chart_only_dashboard_operations plan
```

`plan`은 feature를 켜거나 release·DE440s·key·store를 열지 않는다. key 생성 CLI는 기존 파일을 덮어쓰지 않으며 `CREATE_CHART_ONLY_SECRET_KEY` 확인값을 요구한다. private root와 key directory는 먼저 0700으로 만들고 아래 `$SAJU_DASHBOARD_PRIVATE_ROOT`에는 Git 제외 경로만 사용한다.

```bash
.venv/bin/python -m scripts.runtime.chart_only_operations create-key \
  --purpose runtime-hmac \
  --path "$SAJU_DASHBOARD_PRIVATE_ROOT/keys/runtime-hmac.key" \
  --confirm CREATE_CHART_ONLY_SECRET_KEY
.venv/bin/python -m scripts.runtime.chart_only_operations create-key \
  --purpose session-aead \
  --path "$SAJU_DASHBOARD_PRIVATE_ROOT/keys/session-aead.key" \
  --confirm CREATE_CHART_ONLY_SECRET_KEY
.venv/bin/python -m scripts.runtime.chart_only_operations verify-key-pair \
  --hmac-key-file "$SAJU_DASHBOARD_PRIVATE_ROOT/keys/runtime-hmac.key" \
  --encryption-key-file "$SAJU_DASHBOARD_PRIVATE_ROOT/keys/session-aead.key"
```

## production 통합 canary

```bash
.venv/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_dashboard_canary validate-contract
.venv/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_dashboard_canary plan
.venv/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_dashboard_canary run --execute \
  --run-root "$SAJU_MODEL_RUN_ROOT" \
  --ephemeris "$SAJU_EPHEMERIS_PATH" \
  --port 8767
```

Gate는 feature off 10, 정상 원국 20, 절입 경계 10, 승인 범위 밖 20, 변조 10, 기간 차단 10, rate/concurrency/process 10, 공개 누출 10의 합성 HTTP 100건과 실제 K0·KI20 GPU 1쌍을 사용한다. 두 모델 출력은 비어 있지 않아야 하고 동일 runtime snapshot hash를 받아야 한다. 출력 내용은 이 Gate의 판정 대상이 아니며 품질을 주장하지 않는다.

공개 경로에는 `aggregate.json`과 `build_manifest.json`만 둔다. case별 입력·응답, 모델 원문, runtime capability, key, ciphertext, private path, 공개 URL은 기록하지 않는다. `verify`는 생성된 새 build 경로를 명시해 실행한다.

## 활성화와 rollback

canary가 실패하면 production process를 시작하지 않는다. 통과 뒤 dashboard를 아래 조건으로 시작한다.

- config는 `configs/model_versions/saju_1b_baseline/phase5-dashboard-v1.9.0.json`을 명시한다.
- 실행 파일은 `scripts/training/phase5_dashboard_v1_9.py`를 사용한다.
- `127.0.0.1`에만 bind하고 외부 공개는 기존 tunnel의 exact HTTPS Origin 하나만 신뢰한다.
- 무인증 공개가 의도된 경우에만 `--allow-unauthenticated-remote`를 사용한다.
- runtime은 `--enable-chart-only-runtime`과 DE440s, 두 key, encrypted store, process lease 경로를 모두 명시한 경우에만 열린다.
- 활성 process에서 합성 정상 원국, 절입 경계, 범위 밖, 변조, 기간 차단, 모델 동일 snapshot과 로그 비노출을 다시 확인한다.

rollback은 v1.9 process를 중지하고 보존된 v1.8 config를 runtime flag 없이 다시 시작하는 명시적 전환이다. tunnel은 dashboard loopback port만 가리키므로 정상 rollback에는 tunnel 재발급이 필요하지 않다. v1.9의 암호화 session은 v1.8에 이관하지 않는다.

이 제한 활성화는 strict/full runtime, 기간 계산, Phase 6 결정, MIX20K-v3.1 생성, 추가 학습, 모델 승격 권한을 변경하지 않는다.
