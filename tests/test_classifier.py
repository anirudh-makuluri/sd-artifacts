import pytest

from graph.nodes.classifier import (
    _classify_deploy_shape,
    _classify_single_unit,
    _unit_name_from_root,
    classifier_node,
)


def test_unit_name_from_root_defaults_to_app_for_repo_root():
    assert _unit_name_from_root(".") == "app"
    assert _unit_name_from_root("apps/web") == "web"


def test_classify_deploy_shape_multi_and_static():
    units = [
        {"name": "web", "type": "server"},
        {"name": "api", "type": "server"},
    ]
    assert _classify_deploy_shape(units) == "multi"
    assert _classify_deploy_shape([{"name": "site", "type": "static"}]) == "static"
    assert _classify_deploy_shape([{"name": "app", "type": "static_build"}]) == "static_build"
    assert _classify_deploy_shape([{"name": "app", "type": "existing_docker"}]) == "existing_docker"


def test_classify_single_unit_detects_node_server():
    scan = {
        "key_files": {
            "package.json": '{"name":"demo","scripts":{"start":"node index.js"},"dependencies":{"express":"^4"}}',
        }
    }
    unit = _classify_single_unit(scan, ".", ".")
    assert unit["provider"] == "node"
    assert unit["type"] == "server"
    assert unit["framework"] == "express"
    assert unit["name"] == "app"
    assert unit["port"] == 3000


@pytest.mark.parametrize("files", [
    {"package.json": '{"scripts":{"build":"vite build"},"devDependencies":{"vite":"latest"}}',
     "vite.config.ts": "export default {server:{port:5173}}"},
    {"index.html": "<html>Static site</html>"},
])
def test_static_units_do_not_invent_a_runtime_port(files):
    from models.schemas import DeployUnit

    unit = _classify_single_unit({"key_files": files}, ".", ".")
    assert unit["type"] in {"static", "static_build"}
    assert unit["port"] is None
    assert DeployUnit(**unit).model_dump()["port"] is None


def test_classifier_node_sets_deploy_units_for_simple_repo():
    state = {
        "repo_scan": {
            "key_files": {
                "package.json": '{"name":"demo","scripts":{"dev":"vite"},"dependencies":{"vite":"^5"}}',
                "vite.config.ts": "export default {}",
            }
        },
        "package_path": ".",
    }
    result = classifier_node(state)
    assert result["deploy_shape"] == "static_build"
    assert len(result["deploy_units"]) == 1
    assert result["deploy_units"][0]["framework"] == "vite"
