import json

from tools.evaluate_scan_quality import (
    _build_artifact_summary,
    _build_repo_report,
    _failure_bucket_from_report,
    _load_labels,
    _select_compose_file,
    _select_dockerfile,
    _select_nginx_file,
)


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
    assert labels[0]["artifact_expectations"] == {}
    assert labels[0]["artifact_scoring_overrides"] == {}


def test_load_labels_accepts_artifact_expectation_fields(tmp_path):
    labels_path = tmp_path / "labels.json"
    labels_path.write_text(
        json.dumps(
            {
                "repos": [
                    {
                        "repo": "vercel/next.js",
                        "artifact_expectations": {
                            "dockerfile": {"required_instructions": ["HEALTHCHECK"]},
                            "compose": {"required_services": ["web"]},
                        },
                        "artifact_scoring_overrides": {
                            "dockerfile": {"healthcheck": 0.30},
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    labels = _load_labels(str(labels_path))

    assert len(labels) == 1
    assert labels[0]["artifact_expectations"]["dockerfile"]["required_instructions"] == ["HEALTHCHECK"]
    assert labels[0]["artifact_scoring_overrides"]["dockerfile"]["healthcheck"] == 0.30


def test_failure_bucket_classifies_context_errors():
    report = {
        "error": "No repository context provided to analyze",
        "metrics": {},
    }

    assert _failure_bucket_from_report(report) == "planner_context_missing"


def test_failure_bucket_classifies_port_missing():
    report = {
        "error": None,
        "metrics": {
            "known_port_count": 1,
            "correct_port_count": 0,
            "missing_port_count": 1,
            "false_negatives": 0,
            "true_positives": 1,
            "false_positives": 0,
            "stack_match": True,
        },
    }

    assert _failure_bucket_from_report(report) == "port_missing"


def test_select_compose_file_prefers_package_local_file():
    key_files = {
        "docker-compose.yml": "services: { root: {} }",
        "apps/web/docker-compose.yml": "services: { web: {} }",
    }

    selected_path, selected_content = _select_compose_file(key_files, "apps/web")

    assert selected_path == "apps/web/docker-compose.yml"
    assert "web" in selected_content


def test_build_repo_report_includes_compose_artifact_score():
    repo_result = {
        "repo": "example/repo",
        "package_path": ".",
        "error": None,
        "services": [{"name": "web", "build_context": ".", "port": 3000}],
        "stack_summary": "Node.js, Next.js",
        "stack_tokens": ["node", "next"],
        "key_files": {
                        "Dockerfile": """
FROM node:20-alpine
USER node
EXPOSE 3000
HEALTHCHECK CMD wget -qO- http://localhost:3000 || exit 1
""",
            "docker-compose.yml": """
services:
  web:
    build: .
    ports:
      - \"3000:3000\"
""",
                        "nginx.conf": """
events { worker_connections 1024; }
http {
    server {
        location / {
            proxy_pass http://web:3000;
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection \"upgrade\";
            add_header X-Content-Type-Options \"nosniff\" always;
            add_header X-Frame-Options \"SAMEORIGIN\" always;
            add_header Content-Security-Policy \"default-src 'self'\" always;
        }
    }
}
""",
        },
    }
    label = {
        "expected_services": [{"name": "web", "build_context": "."}],
        "excluded_services": [],
        "required_stack_tokens": ["node"],
        "expected_ports": {"web": 3000},
    }

    report = _build_repo_report(repo_result, label)

    compose = report["artifact_scores"]["compose"]
    assert compose["file_path"] == "docker-compose.yml"
    assert compose["total_score"] > 0.0
    assert "criteria_scores" in compose
    assert "criterion_reasons" in compose

    dockerfile = report["artifact_scores"]["dockerfile"]
    assert dockerfile["file_path"] == "Dockerfile"
    assert dockerfile["total_score"] > 0.0
    assert "criteria_scores" in dockerfile
    assert "criterion_reasons" in dockerfile

    nginx = report["artifact_scores"]["nginx"]
    assert nginx["file_path"] == "nginx.conf"
    assert nginx["total_score"] > 0.0
    assert "criteria_scores" in nginx
    assert "criterion_reasons" in nginx


def test_select_dockerfile_prefers_package_local_file():
    key_files = {
        "Dockerfile": "FROM node:20-alpine",
        "apps/web/Dockerfile": "FROM node:20-alpine\nEXPOSE 3000",
    }

    selected_path, selected_content = _select_dockerfile(key_files, "apps/web")

    assert selected_path == "apps/web/Dockerfile"
    assert "EXPOSE 3000" in selected_content


def test_select_nginx_file_prefers_package_local_file():
    key_files = {
        "nginx.conf": "events {}",
        "apps/web/nginx/default.conf": "events {}\nhttp { server { location / { proxy_pass http://web:3000; } } }",
    }

    selected_path, selected_content = _select_nginx_file(key_files, "apps/web")

    assert selected_path == "apps/web/nginx/default.conf"
    assert "proxy_pass" in selected_content


def test_build_artifact_summary_includes_nginx_and_combined():
    scored_reports = [
        {
            "artifact_scores": {
                "dockerfile": {"file_path": "Dockerfile", "total_score": 1.0, "passed_threshold": True},
                "compose": {"file_path": "docker-compose.yml", "total_score": 0.9, "passed_threshold": True},
                "nginx": {"file_path": "nginx.conf", "total_score": 0.8, "passed_threshold": False},
            }
        },
        {
            "artifact_scores": {
                "dockerfile": {"file_path": None, "total_score": 0.0, "passed_threshold": False},
                "compose": {"file_path": "compose.yml", "total_score": 1.0, "passed_threshold": True},
                "nginx": {"file_path": "default.conf", "total_score": 1.0, "passed_threshold": True},
            }
        },
    ]

    summary = _build_artifact_summary(scored_reports)

    assert summary["dockerfile"]["scored_repo_count"] == 1
    assert summary["compose"]["scored_repo_count"] == 2
    assert summary["nginx"]["scored_repo_count"] == 2
    assert summary["combined"]["scored_repo_count"] == 2
    assert summary["combined"]["all_present_artifacts_pass_rate"] == 0.5