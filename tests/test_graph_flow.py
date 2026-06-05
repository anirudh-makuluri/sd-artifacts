from graph.graph import check_fatal_error, check_scanner_error


def test_check_scanner_error_routes_on_error():
    assert check_scanner_error({"error": "scan failed"}) == "error_or_cached"


def test_check_scanner_error_routes_on_cached_response():
    assert check_scanner_error({"cached_response": {"schema_version": 2}}) == "error_or_cached"


def test_check_scanner_error_continues_without_error_or_cache():
    assert check_scanner_error({}) == "continue"


def test_check_fatal_error_routes_on_error():
    assert check_fatal_error({"error": "clone failed"}) == "error"


def test_check_fatal_error_continues_without_error():
    assert check_fatal_error({"repo_dir": "/tmp/repo"}) == "continue"
