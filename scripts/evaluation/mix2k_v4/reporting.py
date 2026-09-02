# reporting.py - 5-arm deterministic·이중 품질평가를 집계하고 release blocker를 판정한다.

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from scripts.data.mix2k_v4_contracts import normalize_answer, sha256_bytes
from scripts.runtime.calculation.canonical import canonical_json_bytes

from .contracts import EXPECTED_METRICS, REGRESSION_ID, Mix2KV4EvaluationError


def _rate(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 6) if denominator else None,
    }


def _review_lookup(
    reviews_by_provider: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[tuple[str, str], list[Mapping[str, Any]]]:
    values: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for provider, reviews in reviews_by_provider.items():
        for review in reviews:
            if review.get("provider") != provider:
                raise Mix2KV4EvaluationError(
                    "품질 review provider identity가 다릅니다."
                )
            case_id = review.get("case_id")
            scores = review.get("scores")
            if not isinstance(case_id, str) or not isinstance(scores, Mapping):
                raise Mix2KV4EvaluationError("품질 review 구조가 다릅니다.")
            for arm_id, score in scores.items():
                if not isinstance(arm_id, str) or not isinstance(score, Mapping):
                    raise Mix2KV4EvaluationError("품질 review arm score가 다릅니다.")
                values[(case_id, arm_id)].append(score)
    return values


def _cross_case_repetitive_turns(
    rows: Sequence[Mapping[str, Any]], maximum_multiplicity: int
) -> set[tuple[str, int]]:
    occurrences: defaultdict[str, list[tuple[str, int]]] = defaultdict(list)
    for row in rows:
        for turn in row["turns"]:
            normalized = normalize_answer(turn["output"])
            if normalized:
                occurrences[normalized].append((row["case_id"], turn["turn_index"]))
    return {
        identity
        for identities in occurrences.values()
        if len({case_id for case_id, _ in identities}) > maximum_multiplicity
        for identity in identities
    }


def aggregate_arm(
    rows: Sequence[Mapping[str, Any]],
    *,
    reviews: Mapping[tuple[str, str], Sequence[Mapping[str, Any]]],
    arm_id: str,
    repetition: Mapping[str, Any],
) -> dict[str, Any]:
    turns = [turn for row in rows for turn in row["turns"]]
    runtime_turns = [
        turn
        for row in rows
        if row["runtime_snapshot_sha256"] is not None
        for turn in row["turns"]
    ]
    schema_turns = [
        turn
        for row in rows
        if row["axis"] == "schema_literacy"
        for turn in row["turns"]
    ]
    followup_turns = [
        turn for row in rows for turn in row["turns"] if turn["turn_index"] > 0
    ]
    general_turns = [
        turn
        for row in rows
        if row["axis"] == "general_empathy"
        for turn in row["turns"]
    ]
    correct_schema = sum(
        max(0, turn["grade"]["schema_claims"] - turn["grade"]["schema_claim_errors"])
        for turn in schema_turns
    )
    wrong_schema = sum(
        turn["grade"]["schema_claim_errors"]
        + len(turn["grade"]["provided_fact_omissions"])
        for turn in schema_turns
    )
    cross_case = _cross_case_repetitive_turns(
        rows, int(repetition["normalized_cross_case_multiplicity_maximum"])
    )
    within_case: set[tuple[str, int]] = set()
    for row in rows:
        normalized = [normalize_answer(turn["output"]) for turn in row["turns"]]
        repeated_values = {
            value for value in normalized if value and normalized.count(value) > 1
        }
        within_case.update(
            (row["case_id"], turn["turn_index"])
            for turn, value in zip(row["turns"], normalized, strict=True)
            if value in repeated_values
        )
    repetitive = {
        (row["case_id"], turn["turn_index"])
        for row in rows
        for turn in row["turns"]
        if turn["grade"]["within_response_repetitive"]
        or (row["case_id"], turn["turn_index"]) in cross_case
        or (row["case_id"], turn["turn_index"]) in within_case
    }
    review_scores = [
        score for row in rows for score in reviews.get((row["case_id"], arm_id), ())
    ]
    natural_scores = [int(score["natural_explanation"]) for score in review_scores]
    task_fulfillment_scores = [
        int(score["task_fulfillment"]) for score in review_scores
    ]
    ranks = [int(score["preference_rank"]) for score in review_scores]
    first_places = sum(value == 1 for value in ranks)
    metrics = {
        "schema_field_accuracy": _rate(correct_schema, correct_schema + wrong_schema),
        "natal_period_label_confusion": _rate(
            sum(
                turn["grade"]["natal_period_label_confusion"] for turn in runtime_turns
            ),
            len(runtime_turns),
        ),
        "unsupported_fact_rate": _rate(
            sum(turn["grade"]["unsupported_fact"] for turn in runtime_turns),
            len(runtime_turns),
        ),
        "provided_fact_omission_rate": _rate(
            sum(
                bool(turn["grade"]["provided_fact_omissions"]) for turn in runtime_turns
            ),
            len(runtime_turns),
        ),
        "natural_explanation_preference": {
            "reviews": len(natural_scores),
            "mean_score": round(mean(natural_scores), 6) if natural_scores else None,
            "mean_preference_rank": round(mean(ranks), 6) if ranks else None,
            "first_place_rate": round(first_places / len(ranks), 6) if ranks else None,
        },
        "followup_evidence_consistency": {
            **_rate(
                sum(
                    turn["grade"]["followup_evidence_consistent"] is True
                    for turn in followup_turns
                ),
                len(followup_turns),
            ),
            "review_mean_score": (
                round(
                    mean(
                        int(score["followup_quality"])
                        for score in review_scores
                        if score["followup_quality"] is not None
                    ),
                    6,
                )
                if any(score["followup_quality"] is not None for score in review_scores)
                else None
            ),
        },
        "general_conversation_retention": {
            **_rate(
                sum(
                    turn["grade"]["general_conversation_retention_deterministic"]
                    is True
                    for turn in general_turns
                ),
                len(general_turns),
            ),
            "review_mean_score": (
                round(
                    mean(
                        int(score["general_conversation_retention"])
                        for score in review_scores
                        if score["general_conversation_retention"] is not None
                    ),
                    6,
                )
                if any(
                    score["general_conversation_retention"] is not None
                    for score in review_scores
                )
                else None
            ),
        },
        "repetitive_template_response_rate": _rate(len(repetitive), len(turns)),
        "false_saju_injection": _rate(
            sum(turn["grade"]["false_saju_injection"] for turn in general_turns),
            len(general_turns),
        ),
        "reask_rate": _rate(
            sum(turn["grade"]["reasked_bound_input"] for turn in runtime_turns),
            len(runtime_turns),
        ),
    }
    if list(metrics) != EXPECTED_METRICS:
        raise Mix2KV4EvaluationError("10개 평가 지표 순서·집합이 다릅니다.")
    regression = next((row for row in rows if row["case_id"] == REGRESSION_ID), None)
    return {
        "cases": len(rows),
        "turns": len(turns),
        "deterministic_turn_pass_rate": _rate(
            sum(turn["grade"]["deterministic_turn_pass"] for turn in turns), len(turns)
        ),
        "supplemental_quality_review": {
            "task_fulfillment_reviews": len(task_fulfillment_scores),
            "task_fulfillment_mean_score": (
                round(mean(task_fulfillment_scores), 6)
                if task_fulfillment_scores
                else None
            ),
            "task_fulfillment_minimum_score": (
                min(task_fulfillment_scores) if task_fulfillment_scores else None
            ),
        },
        "metrics": metrics,
        "actual_regression_all_turns_pass": bool(
            regression is not None
            and regression["turns"]
            and all(
                turn["grade"]["regression_turn_pass"] is True
                for turn in regression["turns"]
            )
        ),
    }


def _quality_values(arm: Mapping[str, Any]) -> dict[str, float | None]:
    metrics = arm["metrics"]
    return {
        "deterministic_turn_pass_rate": arm["deterministic_turn_pass_rate"]["rate"],
        "schema_field_accuracy": metrics["schema_field_accuracy"]["rate"],
        "natal_period_label_confusion": metrics["natal_period_label_confusion"]["rate"],
        "unsupported_fact_rate": metrics["unsupported_fact_rate"]["rate"],
        "provided_fact_omission_rate": metrics["provided_fact_omission_rate"]["rate"],
        "natural_explanation_mean_score": metrics["natural_explanation_preference"][
            "mean_score"
        ],
        "task_fulfillment_mean_score": arm["supplemental_quality_review"][
            "task_fulfillment_mean_score"
        ],
        "task_fulfillment_minimum_score": arm["supplemental_quality_review"][
            "task_fulfillment_minimum_score"
        ],
        "followup_evidence_consistency": metrics["followup_evidence_consistency"][
            "rate"
        ],
        "followup_review_mean": metrics["followup_evidence_consistency"][
            "review_mean_score"
        ],
        "general_conversation_retention": metrics["general_conversation_retention"][
            "rate"
        ],
        "general_conversation_review_mean": metrics["general_conversation_retention"][
            "review_mean_score"
        ],
        "repetitive_template_response_rate": metrics[
            "repetitive_template_response_rate"
        ]["rate"],
        "false_saju_injection": metrics["false_saju_injection"]["rate"],
        "reask_rate": metrics["reask_rate"]["rate"],
    }


def _release_quality_checks(
    *,
    primary: Mapping[str, Any],
    baseline: Mapping[str, Any],
    release: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    primary_values = _quality_values(primary)
    baseline_values = _quality_values(baseline)
    thresholds = release["metric_thresholds"]
    absolute_fields = {
        "schema_field_accuracy_minimum": ("schema_field_accuracy", ">="),
        "natal_period_label_confusion_maximum": (
            "natal_period_label_confusion",
            "<=",
        ),
        "unsupported_fact_rate_maximum": ("unsupported_fact_rate", "<="),
        "provided_fact_omission_rate_maximum": (
            "provided_fact_omission_rate",
            "<=",
        ),
        "natural_explanation_mean_score_minimum": (
            "natural_explanation_mean_score",
            ">=",
        ),
        "task_fulfillment_mean_score_minimum": (
            "task_fulfillment_mean_score",
            ">=",
        ),
        "task_fulfillment_minimum_score_minimum": (
            "task_fulfillment_minimum_score",
            ">=",
        ),
        "followup_evidence_consistency_minimum": (
            "followup_evidence_consistency",
            ">=",
        ),
        "followup_review_mean_minimum": ("followup_review_mean", ">="),
        "general_conversation_retention_minimum": (
            "general_conversation_retention",
            ">=",
        ),
        "general_conversation_review_mean_minimum": (
            "general_conversation_review_mean",
            ">=",
        ),
        "repetitive_template_response_rate_maximum": (
            "repetitive_template_response_rate",
            "<=",
        ),
        "false_saju_injection_maximum": ("false_saju_injection", "<="),
        "reask_rate_maximum": ("reask_rate", "<="),
    }
    absolute: dict[str, Any] = {}
    for contract_name, (metric_name, operator) in absolute_fields.items():
        value = primary_values[metric_name]
        threshold = float(thresholds[contract_name])
        passed = bool(
            value is not None
            and (
                (operator == ">=" and value >= threshold)
                or (operator == "<=" and value <= threshold)
            )
        )
        absolute[metric_name] = {
            "value": value,
            "operator": operator,
            "threshold": threshold,
            "passed": passed,
        }

    margins = release["k0_noninferiority"]
    noninferiority_fields = {
        "deterministic_turn_pass_rate_margin": "deterministic_turn_pass_rate",
        "natural_explanation_mean_score_margin": ("natural_explanation_mean_score"),
        "general_conversation_retention_margin": "general_conversation_retention",
        "general_conversation_review_mean_margin": ("general_conversation_review_mean"),
        "followup_review_mean_margin": "followup_review_mean",
    }
    noninferiority: dict[str, Any] = {}
    for contract_name, metric_name in noninferiority_fields.items():
        value = primary_values[metric_name]
        baseline_value = baseline_values[metric_name]
        margin = float(margins[contract_name])
        passed = bool(
            value is not None
            and baseline_value is not None
            and value + margin >= baseline_value
        )
        noninferiority[metric_name] = {
            "value": value,
            "k0_value": baseline_value,
            "allowed_margin": margin,
            "passed": passed,
        }
    absolute_pass = all(item["passed"] is True for item in absolute.values())
    noninferiority_pass = all(
        item["passed"] is True for item in noninferiority.values()
    )
    return absolute, noninferiority, absolute_pass, noninferiority_pass


def build_aggregate(
    *,
    eval_id: str,
    identity: Mapping[str, Any],
    rows_by_arm: Mapping[str, Sequence[Mapping[str, Any]]],
    reviews_by_provider: Mapping[str, Sequence[Mapping[str, Any]]],
    config: Mapping[str, Any],
) -> dict[str, Any]:
    review_lookup = _review_lookup(reviews_by_provider)
    arms = {
        arm_id: aggregate_arm(
            rows,
            reviews=review_lookup,
            arm_id=arm_id,
            repetition=config["repetition"],
        )
        for arm_id, rows in rows_by_arm.items()
    }
    expected_reviews = (
        len(rows_by_arm)
        * len(next(iter(rows_by_arm.values())))
        * len(config["quality_review"]["providers"])
    )
    observed_reviews = sum(len(values) for values in review_lookup.values())
    reviews_complete = (
        set(reviews_by_provider) == set(config["quality_review"]["providers"])
        and observed_reviews == expected_reviews
        and all(
            len(review_lookup[(row["case_id"], arm_id)])
            == config["quality_review"]["minimum_reviews_per_case"]
            for arm_id, rows in rows_by_arm.items()
            for row in rows
        )
    )
    primary = config["release"]["primary_arm"]
    regression_reviews = review_lookup.get((REGRESSION_ID, primary), ())
    minimum = config["release"]["regression_review_score_minimum"]
    regression_review_pass = bool(
        len(regression_reviews) == config["quality_review"]["minimum_reviews_per_case"]
        and all(
            score["natural_explanation"] >= minimum
            and score["task_fulfillment"] >= minimum
            and score["followup_quality"] is not None
            and score["followup_quality"] >= minimum
            for score in regression_reviews
        )
    )
    (
        metric_threshold_results,
        k0_noninferiority_results,
        all_metric_thresholds_pass,
        k0_noninferiority_pass,
    ) = _release_quality_checks(
        primary=arms[primary],
        baseline=arms["K0"],
        release=config["release"],
    )
    primary_gate = bool(
        reviews_complete
        and arms[primary]["actual_regression_all_turns_pass"]
        and regression_review_pass
        and all_metric_thresholds_pass
        and k0_noninferiority_pass
    )
    serving_contract = config["release"]["serving_contract"]
    serving_contract_passed = bool(serving_contract["prompt_upgrade_completed"])
    production_release_ready = bool(primary_gate and serving_contract_passed)
    status = (
        "RELEASE_BLOCKED"
        if not primary_gate
        else (
            "EVALUATED_CANDIDATE"
            if production_release_ready
            else "SERVING_INTEGRATION_BLOCKED"
        )
    )
    return {
        "schema_version": "1.0.0",
        "evaluation_id": eval_id,
        "evaluation_identity_sha256": sha256_bytes(canonical_json_bytes(identity)),
        "status": status,
        "arms": arms,
        "quality_review": {
            "providers_required": config["quality_review"]["providers"],
            "providers_completed": sorted(reviews_by_provider),
            "reviews_complete": reviews_complete,
            "observed_arm_case_reviews": observed_reviews,
            "expected_arm_case_reviews": expected_reviews,
            "external_subscription_review_performed": True,
            "candidate_outputs_transmitted": config["quality_review"][
                "external_transmission"
            ]["candidate_outputs_transmitted"],
            "pii_and_restricted_preflight_required": config["quality_review"][
                "external_transmission"
            ]["pii_and_restricted_preflight_required"],
            "explicit_operator_approval_required": config["quality_review"][
                "external_transmission"
            ]["explicit_operator_approval_required"],
            "heuristic_scan_cannot_exclude_memorization": config["quality_review"][
                "external_transmission"
            ]["heuristic_scan_cannot_exclude_memorization"],
            "provider_tool_access_disabled": config["quality_review"][
                "external_transmission"
            ]["provider_tool_access_disabled"],
        },
        "release": {
            "primary_arm": primary,
            "primary_candidate_gate_passed": primary_gate,
            "serving_contract_passed": serving_contract_passed,
            "production_release_ready": production_release_ready,
            "serving_contract": serving_contract,
            "actual_regression_all_turns_pass": arms[primary][
                "actual_regression_all_turns_pass"
            ],
            "actual_regression_dual_review_pass": regression_review_pass,
            "all_metric_thresholds_pass": all_metric_thresholds_pass,
            "metric_threshold_results": metric_threshold_results,
            "k0_noninferiority_pass": k0_noninferiority_pass,
            "k0_noninferiority_results": k0_noninferiority_results,
            "loss_only_selection_performed": False,
            "automatic_production_promotion_allowed": False,
            "production_promotion_performed": False,
        },
        "raw_outputs_included": False,
        "case_ids_included": False,
        "sealed_blind_accessed": False,
    }


__all__ = ["aggregate_arm", "build_aggregate"]
