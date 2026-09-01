<!-- chart_only_operations.md - v1.4 chart-only adapter의 키·암호화 persistence·local canary 운영 경계를 기록한다. -->

# chart-only 운영 준비 계약

이 문서는 `saju-runtime-release-v1.4.0-63dc8d398e90`을 실제 앱에 연결하기 전의 자동 dry-run 경계를 설명한다. 현재 상태는 합성 local canary 통과이며 production application binding은 아니다.

## 고정 보안 경계

- runtime HMAC key와 session encryption key는 서로 다른 현재 사용자 소유 0600 단일-link 일반 파일이어야 한다.
- 두 key는 각각 32바이트이고 경로·내용을 모두 분리한다. HMAC은 식별자 가명화 전용이며 출생 state 암호화를 대신하지 않는다.
- session state는 AES-256-GCM, write마다 새 12바이트 OS 난수 nonce, `schema_version+session_id+key_id` associated data로 암호화한다.
- 저장 root는 0700, record는 0600이며 plaintext·envelope 상한, 최대 100 session, 마지막 write부터 1,800초 보존을 적용한다.
- ciphertext·nonce·runtime ID·birth slot·request body는 운영 로그 허용 필드가 아니다.
- HMAC key rotation은 기존 session ID를 무효화하고 재계산한다. encryption key rotation은 최대 이전 key 1개로 기존 record를 읽은 직후 active key로 재암호화한다.
- key 삭제는 남은 ciphertext에 대한 cryptographic erasure로만 기록한다. SSD·filesystem의 물리적 secure overwrite를 주장하지 않는다.

## 설치·계약 확인

```bash
uv venv --python 3.10 .venv-runtime-adapter
uv pip install --python .venv-runtime-adapter/bin/python \
  -r requirements-runtime-adapter-v1.0.txt

.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations validate-contract
.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations environment
.venv-runtime-adapter/bin/python -m scripts.runtime.chart_only_operations plan
```

`plan`은 feature를 켜거나 release·DE440s·key·store를 열지 않는다. 실제 key 생성 CLI는 기존 파일을 덮어쓰지 않으며 `CREATE_CHART_ONLY_SECRET_KEY` 확인값을 요구한다. 이 저장소 작업에서는 운영 key를 생성하지 않았다.

## 합성 local canary

```bash
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary validate-contract
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary plan
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary run \
  --ephemeris /absolute/private/path/de440s.bsp
.venv-runtime-adapter/bin/python \
  -m scripts.evaluation.saju_runtime.chart_only_canary verify \
  --report-root data/reports/saju_runtime_app_canary/v1.0.0/build-ddde6dce3d3c
```

Gate는 13개 층화 130건을 사용한다. feature 기본 off, 태양력·음력 exact, range, unknown, 절입 격리, 승인 범위 전·후, period 차단, ciphertext 변조, key rotation·분리, 보존·삭제, 공개 응답 누출을 각각 10건씩 확인했다. 결과는 130/130, 실패 0이다.

공개 경로에는 `aggregate.json`과 `build_manifest.json`만 있다. case별 합성 입력·응답, runtime ID, key, ciphertext, private path는 기록하지 않는다. Phase 6 sealed blind, 모델, checkpoint, MIX20K-v3.1과 학습 상태는 읽거나 변경하지 않는다.

## 남은 production 단계

- 운영 secret manager 또는 동등한 보호 경로에서 실제 key provisioning·rotation·폐기 절차를 승인한다.
- 실제 앱 process의 인증·권한·rate limit·동시성 경계에 adapter를 결합한 새 통합 Gate를 만든다.
- feature 기본 off 상태에서 제한 canary를 거친 뒤에만 production binding 여부를 별도로 결정한다.
- 기간 계산은 계속 차단한다. 현재 chart-only release를 MIX20K-v3.1 생성이나 학습 입력으로 사용하지 않는다.
