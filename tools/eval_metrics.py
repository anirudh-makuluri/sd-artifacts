from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

from tools.stack_tokens import normalize_stack_tokens


ServiceKey = Tuple[str, str]
ContextKey = str


@dataclass
class RepoScore:
    repo: str
    predicted_services: Set[ServiceKey]
    expected_services: Set[ServiceKey]
    excluded_services: Set[ServiceKey]
    predicted_contexts: Set[ContextKey]
    expected_contexts: Set[ContextKey]
    excluded_contexts: Set[ContextKey]
    true_positives: int
    false_positives: int
    false_negatives: int
    leaked_mobile_services: int
    predicted_count: int
    stack_match: Optional[bool]
    known_port_count: int
    correct_port_count: int
    missing_port_count: int


def normalize_path(path: str) -> str:
    normalized = (path or ".").replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized or "."


def normalize_name(name: str) -> str:
    return (name or "").strip().lower()


def make_service_key(name: str, build_context: str) -> ServiceKey:
    return (normalize_name(name), normalize_path(build_context))


def service_keys(services: Iterable[Dict]) -> Set[ServiceKey]:
    keys: Set[ServiceKey] = set()
    for service in services or []:
        if not isinstance(service, dict):
            continue
        keys.add(make_service_key(service.get("name", ""), service.get("build_context", ".")))
    return keys


def service_contexts(services: Iterable[Dict]) -> Set[ContextKey]:
    contexts: Set[ContextKey] = set()
    for service in services or []:
        if not isinstance(service, dict):
            continue
        contexts.add(normalize_path(service.get("build_context", ".")))
    return contexts


def _stack_matches(predicted_stack: str, required_tokens: List[str], predicted_stack_tokens: Optional[List[str]] = None) -> Optional[bool]:
    if not required_tokens:
        return None
    required = set(normalize_stack_tokens(required_tokens))
    if predicted_stack_tokens is not None:
        predicted = set(normalize_stack_tokens(predicted_stack_tokens))
        return required.issubset(predicted)
    haystack = (predicted_stack or "").lower()
    return all((token or "").lower() in haystack for token in required)


def _predicted_port_map(predicted_services: Iterable[Dict]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for service in predicted_services or []:
        if not isinstance(service, dict):
            continue
        name = normalize_name(service.get("name", ""))
        port = service.get("port")
        if name and isinstance(port, int):
            result[name] = port
    return result


def _predicted_port_map_by_context(predicted_services: Iterable[Dict]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for service in predicted_services or []:
        if not isinstance(service, dict):
            continue
        build_context = normalize_path(service.get("build_context", "."))
        port = service.get("port")
        if build_context and isinstance(port, int):
            result[build_context] = port
    return result


def score_repo(
    repo: str,
    predicted_services: List[Dict],
    expected_services: List[Dict],
    excluded_services: Optional[List[Dict]] = None,
    required_stack_tokens: Optional[List[str]] = None,
    predicted_stack: str = "",
    predicted_stack_tokens: Optional[List[str]] = None,
    expected_ports: Optional[Dict[str, int]] = None,
) -> RepoScore:
    predicted_keys = service_keys(predicted_services)
    expected_keys = service_keys(expected_services)
    excluded_keys = service_keys(excluded_services or [])

    predicted_contexts = service_contexts(predicted_services)
    expected_contexts = service_contexts(expected_services)
    excluded_contexts = service_contexts(excluded_services or [])

    tp = len(predicted_contexts & expected_contexts)
    fp = len(predicted_contexts - expected_contexts)
    fn = len(expected_contexts - predicted_contexts)
    leaked = len(predicted_contexts & excluded_contexts)

    predicted_port_map = _predicted_port_map(predicted_services)
    predicted_port_map_by_context = _predicted_port_map_by_context(predicted_services)
    known_port_count = 0
    correct_port_count = 0
    missing_port_count = 0

    expected_context_by_name = {
        normalize_name(service.get("name", "")): normalize_path(service.get("build_context", "."))
        for service in expected_services or []
        if isinstance(service, dict)
    }

    for service_name, expected_port in (expected_ports or {}).items():
        norm_name = normalize_name(service_name)
        if not norm_name:
            continue
        known_port_count += 1
        predicted_port = predicted_port_map.get(norm_name)
        if predicted_port is None:
            expected_context = expected_context_by_name.get(norm_name)
            if expected_context:
                predicted_port = predicted_port_map_by_context.get(expected_context)
        if predicted_port is None:
            missing_port_count += 1
        elif predicted_port == expected_port:
            correct_port_count += 1

    stack_match = _stack_matches(predicted_stack, required_stack_tokens or [], predicted_stack_tokens)

    return RepoScore(
        repo=repo,
        predicted_services=predicted_keys,
        expected_services=expected_keys,
        excluded_services=excluded_keys,
        predicted_contexts=predicted_contexts,
        expected_contexts=expected_contexts,
        excluded_contexts=excluded_contexts,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        leaked_mobile_services=leaked,
        predicted_count=len(predicted_keys),
        stack_match=stack_match,
        known_port_count=known_port_count,
        correct_port_count=correct_port_count,
        missing_port_count=missing_port_count,
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return numerator / denominator


def summarize_scores(scores: List[RepoScore]) -> Dict[str, float | int]:
    tp = sum(score.true_positives for score in scores)
    fp = sum(score.false_positives for score in scores)
    fn = sum(score.false_negatives for score in scores)

    precision = _safe_ratio(tp, tp + fp)
    recall = _safe_ratio(tp, tp + fn)
    f1 = 0.0
    if precision + recall > 0:
        f1 = 2 * precision * recall / (precision + recall)

    leaked = sum(score.leaked_mobile_services for score in scores)
    predicted_total = sum(score.predicted_count for score in scores)
    mobile_leakage_rate = _safe_ratio(leaked, predicted_total)

    stack_labeled = [score.stack_match for score in scores if score.stack_match is not None]
    stack_accuracy = 0.0
    if stack_labeled:
        stack_accuracy = sum(1 for matched in stack_labeled if matched) / len(stack_labeled)

    known_port_count = sum(score.known_port_count for score in scores)
    correct_port_count = sum(score.correct_port_count for score in scores)
    missing_port_count = sum(score.missing_port_count for score in scores)
    port_accuracy_known = _safe_ratio(correct_port_count, known_port_count)
    port_unknown_rate = _safe_ratio(missing_port_count, known_port_count)

    return {
        "repos_scored": len(scores),
        "service_true_positives": tp,
        "service_false_positives": fp,
        "service_false_negatives": fn,
        "service_precision": precision,
        "service_recall": recall,
        "service_f1": f1,
        "mobile_leakage_rate": mobile_leakage_rate,
        "stack_accuracy": stack_accuracy,
        "stack_labeled_repo_count": len(stack_labeled),
        "known_port_count": known_port_count,
        "correct_port_count": correct_port_count,
        "port_accuracy_known": port_accuracy_known,
        "port_unknown_rate": port_unknown_rate,
    }
