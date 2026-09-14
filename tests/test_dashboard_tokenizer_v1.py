# test_dashboard_tokenizer_v1.py - tokenizer 파일과 effective backend 변조를 GPU 없이 차단한다.

import hashlib
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from scripts.training import dashboard_tokenizer_v1 as contract


class TokenizerContractTests(unittest.TestCase):
    def test_missing_and_symlink_roots_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(ValueError):
                contract.load_canonical_tokenizer(root / "missing")
            link = root / "alias"
            link.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                contract.load_canonical_tokenizer(link)

    def test_effective_backend_is_checked_even_when_files_match(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hashes = {}
            for name in contract.TOKENIZER_FILES:
                (root / name).write_text("fixture")
                hashes[name] = hashlib.sha256(b"fixture").hexdigest()
            auto = Mock()
            auto.from_pretrained.return_value.backend_tokenizer.to_str.return_value = (
                "patched backend"
            )
            with (
                patch.object(contract, "TOKENIZER_FILES", hashes),
                patch.dict(
                    "sys.modules",
                    {"transformers": types.SimpleNamespace(AutoTokenizer=auto)},
                ),
                self.assertRaisesRegex(ValueError, "backend"),
            ):
                contract.load_canonical_tokenizer(root)
            self.assertIs(
                auto.from_pretrained.call_args.kwargs["fix_mistral_regex"], False
            )

    def test_identity_binds_final_tokens_and_rendered_prompt(self):
        tokenizer = Mock()
        tokenizer.backend_tokenizer.to_str.return_value = "canonical"
        tokenizer.apply_chat_template.return_value = "rendered"
        expected = hashlib.sha256(b"canonical").hexdigest()
        with patch.object(contract, "BACKEND_SHA256", expected):
            first = contract.input_identity(tokenizer, [], [1, 2, 3])
            changed = contract.input_identity(tokenizer, [], [1, 23])
        self.assertEqual(
            first["input_token_ids_sha256"],
            hashlib.sha256(
                json.dumps([1, 2, 3], separators=(",", ":")).encode()
            ).hexdigest(),
        )
        self.assertNotEqual(
            first["input_token_ids_sha256"], changed["input_token_ids_sha256"]
        )
        self.assertEqual(
            first["rendered_prompt_sha256"], hashlib.sha256(b"rendered").hexdigest()
        )
        with self.assertRaises(ValueError):
            contract.input_identity(tokenizer, [], [1])
