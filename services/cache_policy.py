from typing import Any

from services.briefing_policy import is_complete_briefing


def satisfies_analysis_request(result: dict[str, Any], verify_build: bool) -> bool:
    """Reject stale static-port metadata and unrequested build verification."""
    for unit in result.get("deploy_units") or []:
        if unit.get("type") in {"static", "static_build"} and unit.get("port") is not None:
            return False
        if unit.get("type") in {"static", "static_build"} and not is_complete_briefing(result.get("deploy_briefing") or ""):
            return False
    verification = result.get("build_verification") or {}
    return not verify_build or verification.get("requested", True) is not False
