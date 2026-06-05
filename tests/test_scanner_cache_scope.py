from graph.nodes.scanner import scanner_node


class FakeExecuteResponse:
    def __init__(self, data):
        self.data = data


class FakeTableQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def select(self, _columns):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def execute(self):
        rows = [row for row in self.rows if self._matches(row)]
        return FakeExecuteResponse(rows)

    def _matches(self, row):
        for key, value in self.filters.items():
            if row.get(key) != value:
                return False
        return True


class FakeSupabase:
    def __init__(self, cache_rows):
        self.cache_rows = cache_rows

    def table(self, table_name):
        assert table_name == "analysis_cache"
        return FakeTableQuery(self.cache_rows)


class _FakeFetchTool:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, _args):
        return dict(self.payload)


def _install_fake_scanner(monkeypatch, scan_payload):
    import graph.nodes.scanner as scanner_module

    monkeypatch.setattr(scanner_module, "fetch_repo_structure", _FakeFetchTool(scan_payload))


def _base_scan():
    return {
        "repo_full_name": "acme/repo",
        "default_branch": "main",
        "commit_sha": "sha-1",
        "language": "TypeScript",
        "key_files": {},
        "dirs": [],
        "tree_entry_count": 10,
        "candidate_package_paths": [],
    }


def test_scanner_cache_hit_requires_schema_version_2(monkeypatch):
    _install_fake_scanner(monkeypatch, _base_scan())
    import graph.nodes.scanner as scanner_module

    monkeypatch.setattr(
        scanner_module,
        "supabase",
        FakeSupabase(
            cache_rows=[
                {
                    "repo_url": "https://github.com/acme/repo",
                    "commit_sha": "sha-1",
                    "package_path": ".",
                    "schema_version": 2,
                    "result": {
                        "schema_version": 2,
                        "deploy_briefing": "FastAPI",
                        "build_status": "passed",
                    },
                }
            ]
        ),
    )

    state = scanner_node({"repo_url": "https://github.com/acme/repo", "package_path": "."})

    assert "error" not in state
    assert state["cached_response"]["schema_version"] == 2
    assert state["cached_response"]["deploy_briefing"] == "FastAPI"


def test_scanner_ignores_v1_cache_rows(monkeypatch):
    _install_fake_scanner(monkeypatch, _base_scan())
    import graph.nodes.scanner as scanner_module

    monkeypatch.setattr(
        scanner_module,
        "supabase",
        FakeSupabase(
            cache_rows=[
                {
                    "repo_url": "https://github.com/acme/repo",
                    "commit_sha": "sha-1",
                    "package_path": ".",
                    "schema_version": 1,
                    "result": {
                        "stack_summary": "Legacy",
                        "services": [],
                        "confidence": 0.9,
                    },
                }
            ]
        ),
    )

    state = scanner_node({"repo_url": "https://github.com/acme/repo", "package_path": "."})

    assert "cached_response" not in state
    assert state["repo_scan"]["commit_sha"] == "sha-1"


def test_scanner_cache_requires_exact_package_path(monkeypatch):
    _install_fake_scanner(
        monkeypatch,
        {
            **_base_scan(),
            "commit_sha": "sha-2",
        },
    )
    import graph.nodes.scanner as scanner_module

    monkeypatch.setattr(
        scanner_module,
        "supabase",
        FakeSupabase(
            cache_rows=[
                {
                    "repo_url": "https://github.com/acme/repo",
                    "commit_sha": "sha-2",
                    "package_path": ".",
                    "schema_version": 2,
                    "result": {
                        "schema_version": 2,
                        "deploy_briefing": "Monorepo root",
                        "package_path": ".",
                    },
                },
                {
                    "repo_url": "https://github.com/acme/repo",
                    "commit_sha": "sha-2",
                    "package_path": "apps/web",
                    "schema_version": 2,
                    "result": {
                        "schema_version": 2,
                        "deploy_briefing": "Next.js app",
                        "package_path": "apps/web",
                    },
                },
            ]
        ),
    )

    state = scanner_node({"repo_url": "https://github.com/acme/repo", "package_path": "apps/web"})

    assert state["cached_response"]["deploy_briefing"] == "Next.js app"
    assert state["cached_response"]["package_path"] == "apps/web"
