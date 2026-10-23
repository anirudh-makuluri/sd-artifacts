from typing import Any


def satisfies_build_request(result: dict[str, Any], verify_build: bool) -> bool:
    """Plan-only cache entries cannot satisfy a request for build verification."""
    verification = result.get("build_verification") or {}
    return not verify_build or verification.get("requested", True) is not False
