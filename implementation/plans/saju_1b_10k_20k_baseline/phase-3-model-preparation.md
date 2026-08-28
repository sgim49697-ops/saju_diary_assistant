# Phase 3. 학습 모델·환경 준비

| 항목 | 값 |
|---|---|
| 실행 상태 | 미시작 |
| 선행 Phase | Phase 2 완료 |
| 입력 | 고정 모델 revision, 호환성 표, 장비 snapshot |
| 출력 | `.venv`, requirements lock, Instruct snapshot, 고정 chat template, 환경 보고서 |
| 완료 Gate | 고정 환경에서 모델·tokenizer·template를 오프라인 재로딩 가능 |
| 웹 확인일 | 2026-08-27 |

## 목적

Full FT 가능성을 시험하기 전에 Python·CUDA wheel·학습 라이브러리·모델 remote code·tokenizer·chat template를 고정한다. 이 Phase에서는 optimizer step을 실행하지 않는다.

## 현재 장비 snapshot

| 항목 | 확인값 |
|---|---|
| OS | WSL2, Linux kernel `6.6.87.2-microsoft-standard-WSL2` |
| GPU | NVIDIA GeForce RTX 5070 Ti |
| VRAM | 16,303 MiB |
| Compute Capability | 12.0 |
| Windows/NVIDIA driver | 591.86 |
| `nvidia-smi` CUDA 표시 | 13.1 |
| WSL RAM | 15GiB, 가용 약 14GiB |
| Swap | 8GiB |
| 로컬 CUDA toolkit | `nvcc` 없음 |
| 디스크 가용 | 약 751GiB |
| Python | 3.10.12 |
| uv | 0.9.26 |

`nvidia-smi`의 CUDA 표시는 driver가 지원하는 최대 runtime 계열이며 로컬 toolkit 설치를 의미하지 않는다. cu128 PyTorch wheel과 bitsandbytes 사전 빌드 wheel을 우선 사용하고, 이 baseline을 위해 source build나 `nvcc` 설치를 자동 수행하지 않는다.

## 고정 모델

| 용도 | 모델 | revision |
|---|---|---|
| 학습 시작점·원본 비교·template 기준 | `kakaocorp/kanana-2-1.3b-instruct` | `bf4786aa2a1908adce942d53976270132732f720` |

Instruct는 `Kanana2TinyForCausalLM` custom hybrid attention 모델이며 공식 카드가 `trust_remote_code=True`와 `transformers >= 4.57`을 요구한다. 모델 config의 최대 context는 32,768이지만 baseline 학습 길이는 메모리 Gate가 고른 512/768/1024 중 하나로 제한한다. Base checkpoint를 받거나 비교군으로 사용하지 않는다.

## 고정 Python 패키지

| 패키지 | 버전 | 근거 |
|---|---:|---|
| `torch` | 2.9.0 cu128 | 프로젝트 전역 규칙과 PyTorch 공식 wheel |
| `torchvision` | 0.24.0 cu128 | torch 2.9.0 짝 버전 |
| `torchaudio` | 2.9.0 cu128 | torch 2.9.0 짝 버전 |
| `transformers` | 4.57.6 | Kanana config 작성 버전, 공식 요구 범위 |
| `trl` | 1.12.0 | `transformers>=4.56.2` 호환 |
| `datasets` | 4.7.0 | TRL 1.12.0 최소 요구 버전 |
| `accelerate` | 1.14.0 | Python 3.10·torch 2.x 호환 |
| `bitsandbytes` | 0.50.2 | cu128 Linux wheel에 sm120 포함 |

새 버전이 있더라도 이 표를 자동 갱신하지 않는다. 보안·호환 문제로 변경이 필요하면 모든 버전을 다시 검증하고 정본 버전을 올린다.

## 환경 생성 계약

실행 단계에서는 다음 순서를 사용한다.

```bash
uv venv .venv
source .venv/bin/activate
which python
python --version

uv pip install --python .venv/bin/python \
  torch==2.9.0 torchvision==0.24.0 torchaudio==2.9.0 \
  --index-url https://download.pytorch.org/whl/cu128

uv pip install --python .venv/bin/python \
  transformers==4.57.6 \
  trl==1.12.0 \
  datasets==4.7.0 \
  accelerate==1.14.0 \
  bitsandbytes==0.50.2

uv pip show --python .venv/bin/python torch transformers trl datasets accelerate bitsandbytes
```

향후 생성할 `requirements.txt`에는 다음 인덱스와 torch 짝 버전을 반드시 포함한다.

```text
--index-url https://download.pytorch.org/whl/cu128
--extra-index-url https://pypi.org/simple
torch==2.9.0
torchvision==0.24.0
torchaudio==2.9.0
transformers==4.57.6
trl==1.12.0
datasets==4.7.0
accelerate==1.14.0
bitsandbytes==0.50.2
```

설치 완료 후 `uv pip freeze` 결과는 별도 lock snapshot에 보관한다. 토큰이나 로컬 절대 cache 경로는 기록하지 않는다.

## 모델 snapshot과 remote code 검토

1. `hf download --dry-run`으로 Instruct weights와 repository file 목록을 확인한다.
2. Instruct를 고정 revision으로 private local model cache에 다운로드한다.
3. `configuration_kanana2_tiny.py`, `modeling_kanana2_tiny.py`의 SHA-256을 기록하고 임의 네트워크·subprocess·파일 삭제 동작이 없는지 읽는다.
4. 이후 학습·평가에서는 고정 local snapshot과 `local_files_only=True`를 사용한다.
5. 모델 license와 README를 snapshot과 함께 보존하되 checkpoint 공개는 별도 승인 전 금지한다.

```bash
hf download kakaocorp/kanana-2-1.3b-instruct \
  --revision bf4786aa2a1908adce942d53976270132732f720 \
  --dry-run
```

## Chat template 결정

Instruct tokenizer의 공식 chat template와 special token 계약을 그대로 사용한다. 다른 checkpoint에서 template를 이식하거나 임의 문법으로 다시 만들지 않는다.

| 항목 | 값 |
|---|---|
| 원본 | `kakaocorp/kanana-2-1.3b-instruct/chat_template.jinja` |
| revision | `bf4786aa2a1908adce942d53976270132732f720` |
| SHA-256 | `b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3` |
| assistant mask | `{% generation %}` / `{% endgeneration %}` 포함 |
| 학습 EOS 후보 | `<|im_end|>` |
| padding token | `<|end_of_text|>` |

template는 snapshot 원본을 사용하고 동일 바이트 사본을 `configs/chat_templates/kanana2_sft.jinja`에 고정할 예정이다. Phase 4에서 두 파일의 hash 일치, render 결과, special token, EOS 종료와 loss mask가 모두 맞아야 채택한다. 검증 실패 시 임의 수정하지 않고 Phase 3으로 돌아와 변경안과 새 hash를 기록한다.

## 환경 검증

Phase 3에서는 설치·로드만 확인한다.

```text
torch.cuda.is_available() == True
torch.version.cuda == 12.8 계열
GPU 이름과 compute capability == RTX 5070 Ti / 12.0
torch.cuda.is_bf16_supported() == True
bitsandbytes CUDA backend import 성공
Instruct config·tokenizer 로드 성공
Instruct parameter count와 dtype 기록
local_files_only 재로딩 성공
```

`nvcc` 부재로 인해 설치가 source build를 요구하면 임의로 toolkit을 설치하지 않고 차단한다.

## 완료 Gate

- [ ] `.venv`의 Python 경로와 모든 패키지 버전이 고정표와 일치한다.
- [ ] torch cu128가 RTX 5070 Ti에서 BF16 tensor 연산을 수행한다.
- [ ] bitsandbytes 8-bit optimizer backend가 sm120에서 초기화된다.
- [ ] Instruct snapshot과 remote code SHA-256을 기록했다.
- [ ] Instruct config·tokenizer를 오프라인으로 다시 로드했다.
- [ ] chat template 원본 revision과 SHA-256을 기록했다.
- [ ] Kanana license 사본이 snapshot과 함께 있다.

## 공식 자료

- [PyTorch 2.9.0 cu128 설치](https://pytorch.org/get-started/previous-versions/)
- [Kanana 2 1.3B Instruct](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/tree/bf4786aa2a1908adce942d53976270132732f720)
- [Kanana 2 1.3B Instruct template](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/blob/bf4786aa2a1908adce942d53976270132732f720/chat_template.jinja)
- [bitsandbytes 0.50.2 설치](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/installation)
- [TRL 1.12.0 SFTTrainer](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-27 | Kanana Instruct 모델 카드·config | 고정 revision, custom code, Transformers 4.57+, 32K context 확인 |
| 2026-08-27 | PyTorch 이전 버전 페이지 | 2.9.0/0.24.0/2.9.0 cu128 짝 확인 |
| 2026-08-27 | PyPI 배포 메타데이터 | TRL 1.12.0과 고정 패키지의 Python·의존 범위 확인 |
| 2026-08-27 | bitsandbytes 0.50.2 | Linux cu128 wheel의 sm120 및 8-bit optimizer 지원 확인 |
| 2026-08-27 | Instruct template | generation mask와 SHA-256 확인 |
