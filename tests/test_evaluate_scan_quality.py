import json

from tools.evaluate_scan_quality import _load_labels


def test_load_labels_prefers_repo_url(tmp_path):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo_url": "https://github.com/vercel/next.js",
                        "package_path": "examples/with-docker",
                        "expected_services": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    labels = _load_labels(str(labels_path))

    assert len(labels) == 1
    assert labels[0]["repo"] == "vercel/next.js"
    assert labels[0]["repo_url"] == "https://github.com/vercel/next.js"
    assert labels[0]["package_path"] == "examples/with-docker"


def test_load_labels_builds_repo_url_from_repo_name(tmp_path):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "tiangolo/full-stack-fastapi-template",
                        "expected_services": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    labels = _load_labels(str(labels_path))

    assert len(labels) == 1
    assert labels[0]["repo"] == "tiangolo/full-stack-fastapi-template"
    assert labels[0]["repo_url"] == "https://github.com/tiangolo/full-stack-fastapi-template"
    assert labels[0]["package_path"] == "."