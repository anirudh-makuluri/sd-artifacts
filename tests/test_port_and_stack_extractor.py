import json

from tools.port_and_stack_extractor import _default_port_from_stack_tokens, _extract_stack_tokens


def test_default_port_prefers_vite_over_generic_frontend_tokens():
    port, source = _default_port_from_stack_tokens(["node", "react", "vite"])

    assert port == 5173
    assert source == "vite"


def test_default_port_prefers_uvicorn_fastapi_backend_defaults():
    port, source = _default_port_from_stack_tokens(["python", "fastapi", "uvicorn"])

    assert port == 8000
    assert source == "uvicorn"


def test_default_port_prefers_streamlit_when_present():
    port, source = _default_port_from_stack_tokens(["python", "streamlit"])

    assert port == 8501
    assert source == "streamlit"


def test_default_port_prefers_astro_over_generic_node_defaults():
    port, source = _default_port_from_stack_tokens(["node", "react", "astro"])

    assert port == 4321
    assert source == "astro"


def test_extract_stack_tokens_includes_vite_dependency(tmp_path):
    package_json = tmp_path / "package.json"
    package_json.write_text(
        json.dumps(
            {
                "dependencies": {
                    "react": "18.2.0"
                },
                "devDependencies": {
                    "vite": "5.4.0"
                },
            }
        ),
        encoding="utf-8",
    )

    tokens = _extract_stack_tokens(str(tmp_path))

    assert "vite" in tokens
    assert "react" in tokens
    assert "node" in tokens


def test_extract_stack_tokens_detects_bun_package_manager(tmp_path):
    package_json = tmp_path / "package.json"
    package_json.write_text(
        json.dumps(
            {
                "name": "bun-app",
                "packageManager": "bun@1.1.0"
            }
        ),
        encoding="utf-8",
    )

    tokens = _extract_stack_tokens(str(tmp_path))

    assert "bun" in tokens
    assert "node" in tokens


def test_extract_stack_tokens_detects_streamlit_from_pyproject(tmp_path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
dependencies = [
  \"streamlit>=1.38\",
  \"pandas>=2.2\"
]
""".strip(),
        encoding="utf-8",
    )

    tokens = _extract_stack_tokens(str(tmp_path))

    assert "python" in tokens
    assert "streamlit" in tokens