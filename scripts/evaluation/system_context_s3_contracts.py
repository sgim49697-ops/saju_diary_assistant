# system_context_s3_contracts.py - S3 전용 예산·P1·부모 보존·실행 fingerprint를 고정한다.

from pathlib import Path

from scripts.evaluation import system_context_contracts as parent
from scripts.evaluation.system_context_cases import FROZEN_DATE, digest
from scripts.evaluation.system_context_rescore import PARENT_BUILD, PARENT_PINS
from scripts.evaluation.system_context_scoring_v1_1 import SCORER_VERSION

REPO_ROOT = parent.REPO_ROOT
CONFIG = REPO_ROOT / "configs/model_versions/saju_1b_baseline/system-context-s3-v1.0.0.json"
RAW_ROOT = REPO_ROOT / "runs/SYSTEM-CONTEXT-S3/v1.0.0"
PUBLIC_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s3/v1.0.0"
CODE = (
    "scripts/evaluation/system_context_s3.py",
    "scripts/evaluation/system_context_s3_contracts.py",
    "scripts/evaluation/system_context_s3_projection.py",
    "scripts/evaluation/system_context_s3_backend.py",
    "scripts/evaluation/system_context_s3_scoring.py",
    "scripts/evaluation/system_context_rescore.py",
    "scripts/evaluation/system_context_scoring_v1_1.py",
    "tests/test_system_context_s3.py",
    str(CONFIG.relative_to(REPO_ROOT)),
    "configs/chat_prompts/saju_s3_p1_v1.txt",
    "configs/chat_prompts/saju_s3_p1_runtime_v1.txt",
)
GOVERNANCE = {
    "synthetic_only": True,
    "sealed_blind_accessed": False,
    "training_performed": False,
    "service_changed": False,
    "production_promotion_allowed": False,
    "runtime_release_changed": False,
    "confirmation_24_used": False,
    "larger_model_downloaded": False,
    "phase8a_routing_used": False,
}


def validate_contract():
    value = parent.read_json(CONFIG)
    expected = {
        "schema_version": "1.0.0",
        "experiment_id": "saju-system-context-s3-v1.0.0",
        "parent_build": PARENT_BUILD,
        "engine": "lora_r16",
        "context_arm": "C_FULL",
        "instruction_arms": ["P0", "P1"],
        "case_count": 48,
        "maximum_requests": 96,
        "preflight_requests": 0,
        "expected_preblocks": 10,
        "maximum_generations": 86,
        "initial_pair": "longest_eligible_from_main_budget",
        "scoring": SCORER_VERSION,
        "frozen_server_kst_date": FROZEN_DATE.isoformat(),
        "seed": 20260915,
        "minimum_free_vram_mib": 12288,
        "request_timeout_seconds": 300,
        "retry_allowed": False,
        "history_truncation_allowed": False,
        "p1_profile": "configs/chat_prompts/saju_s3_p1_v1.txt",
        "p1_runtime_prefix": "configs/chat_prompts/saju_s3_p1_runtime_v1.txt",
        "raw_root": str(RAW_ROOT.relative_to(REPO_ROOT)),
        "public_root": str(PUBLIC_ROOT.relative_to(REPO_ROOT)),
        "governance": GOVERNANCE,
    }
    if digest(value) != digest(expected):
        raise ValueError("S3 동결 계약·예산·원천·형식 불일치")
    parent.validate_contract()
    for key in ("p1_profile", "p1_runtime_prefix"):
        text = parent.safe_path(REPO_ROOT / value[key]).read_text(encoding="utf-8")
        if not text.strip() or len(text) > 8000:
            raise ValueError("P1 지시문 묶음 파일 오류")
    return value


def build_path(build, *, public=False):
    if not isinstance(build, str) or parent.BUILD_RE.fullmatch(build) is None:
        raise ValueError("S3 build ID 오류")
    return parent.safe_path((PUBLIC_ROOT if public else RAW_ROOT) / build)


def code_fingerprint():
    manifest_path = (
        parent.PUBLIC_ROOT / PARENT_BUILD / "build_manifest.json"
    )
    if parent.file_sha(manifest_path) != PARENT_PINS["build_manifest.json"]:
        raise ValueError("S3 부모 manifest pin 오류")
    originals = parent.read_json(manifest_path)["identity"]["code_sha256"]
    for path, pin in originals.items():
        if parent.file_sha(REPO_ROOT / path) != pin:
            raise ValueError("S3 부모 source 변경")
    return {**originals, **{p: parent.file_sha(REPO_ROOT / p) for p in CODE}}


def verify_model_artifacts(context):
    verified = {}
    for name in ("k0_instruct", "lora_r16"):
        engine = context["inference_engines"]["engines"][name]
        if engine["kind"] == "lora_adapter":
            parent.dashboard._validate_lora_adapter_artifacts(context, engine)
        required = dict(engine.get("required_file_sha256", {}))
        required["adapter_model.safetensors" if name == "lora_r16" else "model.safetensors"] = engine["model_sha256"]
        actual = {key: parent.file_sha(Path(engine["resolved_path"]) / key) for key in required}
        if actual != required:
            raise ValueError("S3 R16 또는 기본 모델 artifact 변조")
        verified[name] = actual
    return verified
