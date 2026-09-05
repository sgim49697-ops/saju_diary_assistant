# dashboard_tokenizer_v1.py - 세 비교 모델의 원본 K0 tokenizer와 실제 입력 identity를 고정한다.

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

TOKENIZER_REVISION = "k0-original-tokenizer-v1.0.0"
BACKEND_SHA256 = "4f1fb83a437cc9c2f262ef579cfd635d355776a5eaf0fffafd9bf2b03487507f"
TOKENIZER_FILES = {
    "tokenizer.json": "1c4be9ecf77c926456fb82d4cf07ff1218a91907f3408f44895d2b01e0f2b5ab",
    "tokenizer_config.json": "1cdee8fcd4f6209e07e6d9966c8a3ff2d738830d79475193e94e448e153ae2d5",
    "chat_template.jinja": "b8ee6b31575eada17ebbe73d3f1ac65d3efde64f0a25ff922031dec7e1cae3e3",
}
K0_RELATIVE = Path(
    "models/saju_1b_baseline/kanana-2-1.3b-instruct/bf4786aa2a1908adce942d53976270132732f720"
)


def backend_sha256(tokenizer: Any) -> str:
    return hashlib.sha256(tokenizer.backend_tokenizer.to_str().encode()).hexdigest()


def load_canonical_tokenizer(root: Path) -> Any:
    if (
        root.is_symlink()
        or not root.is_dir()
        or any(parent.is_symlink() for parent in root.parents)
    ):
        raise ValueError("원본 tokenizer 경로가 안전하지 않습니다.")
    for name, expected in TOKENIZER_FILES.items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(f"원본 tokenizer {name} SHA-256 불일치")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        root, local_files_only=True, trust_remote_code=False, fix_mistral_regex=False
    )
    if backend_sha256(tokenizer) != BACKEND_SHA256:
        raise ValueError("원본 tokenizer effective backend SHA-256 불일치")
    if tokenizer.chat_template != (root / "chat_template.jinja").read_text(
        encoding="utf-8"
    ):
        raise ValueError("원본 tokenizer effective chat template 불일치")
    return tokenizer


def input_identity(
    tokenizer: Any, messages: list[dict[str, str]], token_ids: list[int]
) -> dict[str, Any]:
    backend = backend_sha256(tokenizer)
    if backend != BACKEND_SHA256:
        raise ValueError("생성 직전 tokenizer backend 불일치")
    rendered = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    return {
        "tokenizer_revision": TOKENIZER_REVISION,
        "tokenizer_backend_sha256": backend,
        "rendered_prompt_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
        "input_token_ids_sha256": hashlib.sha256(
            json.dumps(token_ids, separators=(",", ":")).encode()
        ).hexdigest(),
    }
