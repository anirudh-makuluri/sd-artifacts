from typing import Dict, Any
import json
from .llm_config import llm_docker, strip_markdown_wrapper, RETRY_CONFIGS, FALLBACK_PROMPTS
from graph.llm_retry import invoke_with_retry
from tools.example_bank import fetch_reference_examples, format_examples_for_prompt


def dockerfile_generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate production Dockerfiles for each service."""
    scan = state.get("repo_scan", {})
    key_files = scan.get("key_files", {})
    services = state.get("services", [])
    
    dockerfiles = {}
    
    for service in services:
        svc_name = service["name"]
        build_ctx = service["build_context"]
        port = service["port"]
        dockerfile_path = service.get("dockerfile_path", "")
        
        # Look up the pre-existing Dockerfile using the planner-provided path
        existing_dockerfile = None
        if dockerfile_path:
            existing_dockerfile = key_files.get(dockerfile_path)
        
        if existing_dockerfile:
            examples = fetch_reference_examples(
                artifact_type="dockerfile",
                detected_stack=state.get("detected_stack", "unknown"),
                service=service,
                limit=3,
            )
            references = format_examples_for_prompt(examples)

            prompt = f"""
You are a DevOps expert reviewing an existing Dockerfile.

Service: {svc_name}
Build context: {build_ctx}
Port: {port}
Stack: {state.get('detected_stack', 'unknown')}

EXISTING Dockerfile:
{existing_dockerfile}

REFERENCE EXAMPLES (adapt style/patterns, do not copy verbatim):
{references}

Review this Dockerfile. If it follows production best practices (multi-stage builds, non-root user, slim images, proper EXPOSE/HEALTHCHECK), return it AS-IS.
If it can be improved, return the IMPROVED version.

Rules:
1. Use multi-stage builds if not already present.
2. Use slim/alpine base images.
3. Do NOT copy node_modules / venv directly, build inside builder stage.
4. Run as non-root user.
5. EXPOSE the correct port and add HEALTHCHECK.
6. Output ONLY Dockerfile content, no explanations. Do not wrap in markdown.
7. Do NOT include any preamble like 'IMPROVED Dockerfile:' or commentary. Return ONLY the raw Dockerfile.
8. Reuse useful patterns from REFERENCE EXAMPLES where applicable, but do not copy exact text.
"""
        else:
            examples = fetch_reference_examples(
                artifact_type="dockerfile",
                detected_stack=state.get("detected_stack", "unknown"),
                service=service,
                limit=3,
            )
            references = format_examples_for_prompt(examples)

            prompt = f"""
Generate a PRODUCTION Dockerfile.

Service: {svc_name}
Build context: {build_ctx}
Port: {port}
Stack: {state.get('detected_stack', 'unknown')}
Repo scan: {json.dumps(scan, indent=2)}

REFERENCE EXAMPLES (adapt style/patterns, do not copy verbatim):
{references}

Rules:
1. Use multi-stage builds.
2. Use slim/alpine base images.
3. Do NOT copy node_modules / venv directly from host, build inside the builder stage.
4. Run as non-root user.
5. EXPOSE the port and add HEALTHCHECK.
6. Output ONLY Dockerfile content, no explanations. Do not wrap in markdown.
7. Do NOT include any preamble or commentary. Return ONLY the raw Dockerfile.
8. Reuse useful patterns from REFERENCE EXAMPLES where applicable, but do not copy exact text.
"""
        
        try:
            response, _, _ = invoke_with_retry(
                invoke_fn=lambda raw_prompt: llm_docker.invoke(raw_prompt),
                prompt=prompt,
                fallback_prompt=FALLBACK_PROMPTS["docker"],
                config=RETRY_CONFIGS["docker"],
                node_name=f"docker_gen:{svc_name}",
            )
            dockerfiles[svc_name] = strip_markdown_wrapper(response.content)
        except Exception as e:
            state["error"] = f"Failed generating Dockerfile for {svc_name}: {e}"
            return state
    
    state["dockerfiles"] = dockerfiles
    return state
