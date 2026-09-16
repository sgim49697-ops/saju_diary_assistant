# system_context_s4_contracts.py - 기존 S2/S3를 보존하고 S4 비교·권한·192요청 신원을 고정한다.

from scripts.evaluation import system_context_contracts as parent
from scripts.evaluation import system_context_s3_contracts as s3
from scripts.evaluation.system_context_cases import digest
from scripts.evaluation.system_context_s4_models import REGISTRY, registration
from scripts.evaluation.system_context_scoring_v1_2 import SCORER_VERSION

REPO_ROOT = parent.REPO_ROOT
CONFIG = REPO_ROOT / "configs/model_versions/saju_1b_baseline/system-context-s4-v1.0.0.json"
RAW_ROOT = REPO_ROOT / "runs/SYSTEM-CONTEXT-S4/v1.0.0"
PUBLIC_ROOT = REPO_ROOT / "data/reports/saju_1b_baseline/system-context-s4/v1.0.0"
CODE = (
    "scripts/evaluation/system_context_s4.py",
    "scripts/evaluation/system_context_s4_contracts.py",
    "scripts/evaluation/system_context_s4_models.py",
    "scripts/evaluation/system_context_s4_projection.py",
    "scripts/evaluation/system_context_s4_backend.py",
    "scripts/evaluation/system_context_s4_scoring.py",
    "scripts/evaluation/system_context_scoring_v1_2.py",
    "tests/test_system_context_s4.py",
    "tests/test_system_context_s4_audit.py",
    "tests/test_system_context_scoring_v1_2.py",
    str(CONFIG.relative_to(REPO_ROOT)), str(REGISTRY.relative_to(REPO_ROOT)),
)
S3_PUBLIC_PINS = {
    "aggregate.json": "46e83c1e1b787ad1a244b723f3982efa275d042fe04e4cc1ed6d2590368bf3a2",
    "build_manifest.json": "c1e83a8423da5e74a5f9c0eed5de0b7eeca6c851fde7893f4eca3d0122c8e5a0",
}
GOVERNANCE = {
    "synthetic_only": True, "sealed_blind_accessed": False,
    "training_performed": False, "service_changed": False,
    "production_promotion_allowed": False, "runtime_release_changed": False,
    "confirmation_24_used": False, "phase8a_routing_used": False,
    "application_model_registry_changed": False,
}


def validate_contract():
    value = parent.read_json(CONFIG)
    expected = {
        "schema_version": "1.0.0", "experiment_id": "saju-system-context-s4-v1.0.0",
        "parent_build": "build-c39b4bce5089", "s3_build": "build-ffd985905b51",
        "engines": ["k0_instruct", "kanana3b_instruct"], "context_arms": ["C_FULL", "C_MIN"],
        "instruction_arm": "P0", "case_count": 48, "maximum_requests": 192,
        "preflight_requests": 0, "expected_preblocks": 20, "maximum_generations": 172,
        "initial_pair": "longest_eligible_per_model_from_main_budget", "scoring": SCORER_VERSION,
        "frozen_server_kst_date": "2026-09-15", "seed": 20260915,
        "minimum_free_vram_mib": 12288, "request_timeout_seconds": 300,
        "retry_allowed": False, "history_truncation_allowed": False,
        "model_registry": str(REGISTRY.relative_to(REPO_ROOT)),
        "raw_root": str(RAW_ROOT.relative_to(REPO_ROOT)),
        "public_root": str(PUBLIC_ROOT.relative_to(REPO_ROOT)), "governance": GOVERNANCE,
    }
    if digest(value) != digest(expected):
        raise ValueError("S4 동결 계약·192요청·모델·P0 비교 불일치")
    s3.validate_contract()
    registration()
    root = s3.PUBLIC_ROOT / value["s3_build"]
    for name, pin in S3_PUBLIC_PINS.items():
        if parent.file_sha(root / name) != pin:
            raise ValueError("S4 진입 근거인 완료 S3 공개 파일 변조")
    return value


def build_path(build, *, public=False):
    if not isinstance(build, str) or parent.BUILD_RE.fullmatch(build) is None:
        raise ValueError("S4 build ID 오류")
    return parent.safe_path((PUBLIC_ROOT if public else RAW_ROOT) / build)


def code_fingerprint():
    # 부모가 사용한 기존 파일은 모두 검증하되 추가 S4 파일로 부모를 재발행하지 않는다.
    return {**s3.code_fingerprint(), **{p: parent.file_sha(REPO_ROOT / p) for p in CODE}}
