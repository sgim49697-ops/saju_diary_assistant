# Phase 3. 학습 모델·환경 준비

| 항목 | 값 |
|---|---|
| 실행 상태 | 완료 |
| 선행 Phase | Phase 2 완료 |
| 입력 | 고정 모델 revision, 호환성 표, 장비 snapshot |
| 출력 | Git 제외 `.venv`·Instruct snapshot, 해시 lock, 고정 chat template, 검증 보고서 |
| 완료 Gate | 고정 환경에서 모델·tokenizer·template 전체 BF16 오프라인 로드 성공 |
| 웹 확인일 | 2026-08-28 |

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
| 디스크 가용 | 모델·환경 설치 후 약 738GiB |
| Python | 3.10.12 |
| uv | 0.9.26 |

`nvidia-smi`의 CUDA 표시는 driver가 지원하는 최대 runtime 계열이며 로컬 toolkit 설치를 의미하지 않는다. PyTorch 2.13의 안정 CUDA 13.0 wheel과 bitsandbytes 사전 빌드 wheel을 사용했으며, 이 baseline을 위해 source build나 `nvcc` 설치를 수행하지 않았다. PyTorch 2.12 공식 안내가 Blackwell에는 CUDA 13.0+ wheel을 권장하고 Windows driver 580.88 이상을 요구하므로, driver 591.86에서 안정 채널인 `cu130`을 선택했다. CUDA 13.2는 이 장비의 `nvidia-smi` 표시 13.1보다 높고 2.12 기준 실험 빌드였으므로 선택하지 않았다.

## 고정 모델

| 용도 | 모델 | revision |
|---|---|---|
| 학습 시작점·원본 비교·template 기준 | `kakaocorp/kanana-2-1.3b-instruct` | `bf4786aa2a1908adce942d53976270132732f720` |

Instruct는 `Kanana2TinyForCausalLM` custom hybrid attention 모델이며 공식 카드가 `trust_remote_code=True`와 `transformers >= 4.57`을 요구한다. 모델 config의 최대 context는 32,768이지만 baseline 학습 길이는 메모리 Gate가 고른 512/768/1024 중 하나로 제한한다. Base checkpoint를 받거나 비교군으로 사용하지 않는다.

## 고정 Python 패키지

| 패키지 | 버전 | 근거 |
|---|---:|---|
| `torch` | 2.13.0 cu130 | 2026-07-08 최신 안정 릴리스, Blackwell용 안정 CUDA 13.0 wheel |
| `torchvision` | 0.28.0 cu130 | torch 2.13.0 공식 짝 버전 |
| `torchaudio` | 2.11.0 cu130 | stable PyTorch ABI로 torch 2.11 이상 지원 |
| `transformers` | 4.57.6 | Kanana config의 4.57.1 및 모델 카드의 4.57+ 요구를 만족하는 patch 버전 |
| `trl` | 1.12.0 | `transformers>=4.56.2` 호환 |
| `datasets` | 4.7.0 | TRL 1.12.0 최소 요구 버전 |
| `accelerate` | 1.14.0 | Python 3.10·torch 2.x 호환 |
| `bitsandbytes` | 0.50.2 | CUDA 13.0 native library와 sm120 지원 |

새 버전이 있더라도 이 표를 자동 갱신하지 않는다. 보안·호환 문제로 변경이 필요하면 모든 버전을 다시 검증하고 정본 버전을 올린다.

## 환경 생성 계약

실행 단계에서는 다음 순서를 사용한다.

```bash
uv venv .venv --python 3.10
readlink -f .venv/bin/python
.venv/bin/python --version

uv pip compile requirements.txt \
  --python-version 3.10 \
  --python-platform x86_64-unknown-linux-gnu \
  --generate-hashes \
  --output-file requirements-phase3.lock.txt

uv pip install --python .venv/bin/python \
  --require-hashes \
  --only-binary :all: \
  --index-url https://download.pytorch.org/whl/cu130 \
  --extra-index-url https://pypi.org/simple \
  -r requirements-phase3.lock.txt

uv pip check --python .venv/bin/python
uv pip show --python .venv/bin/python \
  torch torchvision torchaudio transformers trl datasets accelerate bitsandbytes
```

고정한 `requirements.txt`에는 다음 인덱스와 직접 의존성을 포함한다.

```text
--index-url https://download.pytorch.org/whl/cu130
--extra-index-url https://pypi.org/simple
--only-binary :all:
torch==2.13.0
torchvision==0.28.0
torchaudio==2.11.0
transformers==4.57.6
trl==1.12.0
datasets==4.7.0
accelerate==1.14.0
bitsandbytes==0.50.2
```

`requirements-phase3.lock.txt`는 Linux x86_64·Python 3.10 대상으로 75개 전이 의존성과 배포 hash를 고정한다. 설치 후 실제 `uv pip freeze`는 공개 보고서의 `package_freeze.txt`에 보관하며 토큰이나 로컬 절대 cache 경로는 기록하지 않는다.

## 모델 snapshot과 remote code 검토

1. 공식 Hugging Face Hub metadata API로 고정 commit과 14개 repository file의 이름·크기, LFS SHA-256을 무쓰기 확인한다.
2. Instruct를 고정 revision으로 private local model cache에 다운로드한다.
3. `configuration_kanana2_tiny.py`, `modeling_kanana2_tiny.py`의 SHA-256을 기록하고 임의 네트워크·subprocess·파일 삭제 동작이 없는지 읽는다.
4. 이후 학습·평가에서는 고정 local snapshot과 `local_files_only=True`를 사용한다.
5. 모델 license와 README를 snapshot과 함께 보존하되 checkpoint 공개는 별도 승인 전 금지한다.

고정한 `huggingface-hub==0.36.2`의 `hf download`에는 `--dry-run`이 없으므로 `scripts/model/phase3_prepare.py`가 `HfApi.model_info(..., files_metadata=True)`로 같은 검사를 수행한다. 실제 다운로드는 임시 경로에서 검증을 마친 뒤에만 Git 제외 최종 경로로 원자 승격한다.

```bash
.venv/bin/python scripts/model/phase3_prepare.py download-model
.venv/bin/python scripts/model/phase3_prepare.py download-model --execute
.venv/bin/python scripts/model/phase3_prepare.py verify-snapshot
```

| 항목 | 고정값 |
|---|---|
| local snapshot | `models/saju_1b_baseline/kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720/` |
| payload | 14파일, 2,593,309,962 bytes |
| manifest SHA-256 | `5786d04831c93192d234651df0894a1912b974cfab96011ce0676563185cc93d` |
| `model.safetensors` SHA-256 | `49aa6cd8686563c59321d83810731956c61ec8d5c8538a249d38007986cdc942` |
| configuration code SHA-256 | `191fb6fbfd63968cc24b3beeb8190aaa88868d4cf1695f8c5a379fb0a077d79d` |
| modeling code SHA-256 | `e47cd8cc99e71fc69eea9bf5ba1221526fb8c6d4fc8677177e82de997b766500` |
| remote code 정적 검토 | network·subprocess·삭제 call 0, 금지 import 0 |

## Chat template 결정

Instruct tokenizer의 공식 chat template와 special token 계약을 그대로 사용한다. 다른 checkpoint에서 template를 이식하거나 임의 문법으로 다시 만들지 않는다.

| 항목 | 값 |
|---|---|
| 원본 | `kakaocorp/kanana-2-1.3b-instruct/chat_template.jinja` |
| revision | `bf4786aa2a1908adce942d53976270132732f720` |
| SHA-256 | `b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3` |
| assistant mask | `{%- generation -%}` / `{%- endgeneration -%}` 포함 |
| 학습 EOS 후보 | `<|im_end|>` |
| padding token | `<|end_of_text|>` |

template는 snapshot 원본을 사용하고 동일한 10,725 bytes 사본을 `configs/chat_templates/kanana2_sft.jinja`에 고정했다. tokenizer의 BOS `<|begin_of_text|>` 128000, EOS `<|im_end|>` 128010, PAD `<|end_of_text|>` 128001도 확인했다. Phase 4에서는 render 결과, EOS 종료와 실제 assistant loss mask를 별도로 검증해야 한다. 그 검증 전에는 학습을 허용하지 않는다.

## 환경 검증

Phase 3에서는 설치·로드만 확인한다.

```text
torch.cuda.is_available() == True
torch.version.cuda == 13.0
GPU 이름과 compute capability == RTX 5070 Ti / 12.0
torch.cuda.get_arch_list()에 sm_120 포함
torch.cuda.is_bf16_supported() == True
bitsandbytes CUDA backend import 성공
Instruct config·tokenizer 로드 성공
Instruct parameter count와 dtype 기록
local_files_only 재로딩 성공
```

`nvcc` 부재로 인해 설치가 source build를 요구하면 임의로 toolkit을 설치하지 않고 차단한다.

## 검증 결과

| 검사 | 결과 |
|---|---|
| Python·uv·직접 패키지 | 3.10.12, uv 0.9.26, 고정 8개 버전 일치, `uv pip check` 통과 |
| PyTorch domain import | torch `2.13.0+cu130`, torchvision `0.28.0+cu130` native ops, torchaudio `2.11.0+cu130` import 성공 |
| CUDA·GPU | `torch.version.cuda=13.0`, RTX 5070 Ti, CC 12.0, `sm_120`, 17,094,475,776 bytes |
| BF16 | 지원 확인 및 128×128 CUDA matmul 통과 |
| bitsandbytes | `libbitsandbytes_cuda130.so`, Adam8bit CUDA uint8 state 초기화 통과, step 미실행 |
| config·tokenizer | offline·`local_files_only=True` 로드 및 template·special token 일치 |
| 전체 모델 load | `Kanana2TinyForCausalLM`, 1,291,478,272개 parameter 전부 BF16·CUDA |
| load key | missing·unexpected·mismatched·error 0 |
| VRAM | peak allocated 2,617,445,888 bytes, peak reserved 2,619,342,848 bytes |
| 실행 경계 | forward·generation·optimizer step·학습 모두 미실행 |

검증 보고서는 `data/reports/saju_1b_baseline/model-preparation/v1.0.0/build-32e2c84af3d3/`에 고정했다. build SHA-256은 `32e2c84af3d3da1c2938f21d368453185535340fbbb281382a1051a2f789451d`, package freeze SHA-256은 `105bdc62957e0bd66dd6cc82600918918148645d3d7a959dc33eea2aa3567c8a`다. 보고서에는 모델 가중치가 없으며 `training_promotion_allowed=false`다.

고정 upstream config는 full-attention YaRN의 명시적 `factor=40.0`과 `32768/4096=8.0` 비율이 다르다는 Transformers 경고를 낸다. snapshot과 remote code hash를 보존하기 위해 값을 덮어쓰지 않았고, 보고서에 `ratio_matches_explicit_factor=false`로 기록했다. Phase 3의 config·tokenizer·전체 parameter 로드에는 영향이 없었으며 forward나 generation은 이 Phase 범위 밖이라 실행하지 않았다.

## 완료 Gate

- [x] `.venv`의 Python 경로와 모든 패키지 버전이 고정표와 일치한다.
- [x] torch cu130가 RTX 5070 Ti에서 native sm120 BF16 tensor 연산을 수행한다.
- [x] bitsandbytes 8-bit optimizer backend가 sm120에서 초기화된다.
- [x] Instruct snapshot과 remote code SHA-256을 기록했다.
- [x] Instruct config·tokenizer·전체 BF16 모델을 오프라인으로 다시 로드했다.
- [x] chat template 원본 revision과 SHA-256을 기록했다.
- [x] Kanana license 사본이 snapshot과 함께 있다.

## 공식 자료

- [PyTorch 2.13 릴리스](https://pytorch.org/blog/pytorch-2-13-release-blog/)
- [PyTorch 2.12 Blackwell·CUDA 13 안내](https://pytorch.org/blog/pytorch-2-12-release-blog/)
- [PyTorch cu130 공식 wheel index](https://download.pytorch.org/whl/cu130/torch/)
- [PyTorch 이전 버전 설치표](https://pytorch.org/get-started/previous-versions/)
- [NVIDIA CUDA GPU compute capability](https://developer.nvidia.com/cuda-gpus)
- [NVIDIA CUDA minor-version compatibility](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)
- [TorchAudio stable ABI 설치표](https://docs.pytorch.org/audio/main/installation.html)
- [Kanana 2 1.3B Instruct](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/tree/bf4786aa2a1908adce942d53976270132732f720)
- [Kanana 2 1.3B Instruct template](https://huggingface.co/kakaocorp/kanana-2-1.3b-instruct/blob/bf4786aa2a1908adce942d53976270132732f720/chat_template.jinja)
- [bitsandbytes 0.50.2 설치](https://huggingface.co/docs/bitsandbytes/v0.50.2/en/installation)
- [TRL 1.12.0 SFTTrainer](https://huggingface.co/docs/trl/v1.12.0/en/sft_trainer)

## 웹 확인 기록

| 날짜 | 확인 내용 | 결과 |
|---|---|---|
| 2026-08-28 | PyTorch 2.13 릴리스·cu130 공식 index | 최신 안정 2.13.0 CPython 3.10 x86_64 wheel과 CUDA 13.0 기본 유지 확인 |
| 2026-08-28 | PyTorch 2.12 Blackwell 안내·NVIDIA 자료 | Blackwell에 CUDA 13.0+ 권장, Windows driver 580.88 이상, RTX 5070 Ti CC 12.0 확인 |
| 2026-08-28 | TorchAudio 설치표 | 2.11.0 stable ABI가 torch 2.11 이상을 지원함을 확인 |
| 2026-08-28 | Kanana Instruct 모델 카드·고정 tree | public·ungated 고정 revision, custom code, Transformers 4.57+, 14파일 확인 |
| 2026-08-28 | bitsandbytes 0.50.2 | CUDA 13.0·sm120 native backend와 8-bit optimizer 지원 확인 |
| 2026-08-28 | Instruct template | 10,725 bytes, generation mask와 SHA-256 일치 확인 |

## 진행 기록

- 2026-08-28
  - 작업 요약: RTX 5070 Ti와 최신 공식 문서를 기준으로 PyTorch 2.13.0 cu130 환경, 고정 Kanana 2 1.3B Instruct snapshot·tokenizer·chat template를 준비하고 전체 BF16 GPU 로드까지 완료했다.
  - 변경 범위: 모델·환경 계약, 직접 requirements와 해시 lock, 안전한 download·snapshot·remote code·GPU smoke·보고서 검증 CLI, 회귀 테스트, 공개 검증 보고서를 추가했다. `.venv`와 2.59GB 모델 payload는 Git 제외 경로에만 두었다.
  - 검증: 75개 package 해시 설치와 `uv pip check`, 공식 Hub metadata dry-run, 14파일 전체 SHA-256, remote code 금지 동작 0, BF16 matmul, bitsandbytes Adam8bit state, 1,291,478,272개 BF16 CUDA parameter 전체 로드, 누락·불일치 key 0, 보고서 재검증을 통과했다.
  - 남은 이슈·후속 작업: upstream YaRN factor 경고는 원본 보존 상태로 기록했다. Phase 4에서만 tokenizer render·assistant loss mask·최종 데이터셋·200-step memory smoke를 검증하며, 현재 `training_promotion_allowed=false`이고 실제 학습은 시작하지 않았다.
