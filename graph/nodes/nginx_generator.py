from typing import Dict, Any
from .llm_config import llm_nginx, strip_markdown_wrapper, RETRY_CONFIGS, FALLBACK_PROMPTS
from graph.llm_retry import invoke_with_retry


def nginx_generator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """Generate an nginx.conf for production deployment with multi-service routing."""
    scan = state.get("repo_scan", {})
    key_files = scan.get("key_files", {})
    services = state.get("services", [])
    
    # Check for existing nginx config
    existing_nginx = None
    for path, content in key_files.items():
        filename = path.split("/")[-1]
        if filename == "nginx.conf":
            existing_nginx = content
            break
    
    services_desc = "\n".join([
        f"  - {s['name']}: port={s['port']}"
        for s in services
    ])
    
    if existing_nginx:
        prompt = f"""
You are a DevOps expert reviewing an existing nginx.conf.

Services:
{services_desc}

EXISTING nginx.conf:
{existing_nginx}

Review this nginx config. If it correctly routes to all services with proper security headers, return it AS-IS.
If it can be improved, return the IMPROVED version.

Rules:
- Listen on port 80.
- Route traffic to each service appropriately (e.g., frontend on /, WebSocket on /ws, API on /api).
- Use `localhost` as upstream hostnames (e.g., `proxy_pass http://localhost:3000`).
- Include ALL of these security headers: X-Frame-Options, X-Content-Type-Options, Content-Security-Policy.
- Include proper proxy headers (X-Real-IP, X-Forwarded-For).
- For WebSocket services, include proper upgrade headers.
- Output ONLY nginx config, no markdown wrappers.
"""
    else:
        prompt = f"""
You are a DevOps expert given the task to write a nginx.conf for production deployment.

Services:
{services_desc}

Rules:
- Listen on port 80.
- Route traffic to each service appropriately (e.g., frontend on /, WebSocket on /ws, API on /api).
- Use `localhost` as upstream hostnames (e.g., `proxy_pass http://localhost:3000`).
- Include ALL of these security headers: X-Frame-Options, X-Content-Type-Options, Content-Security-Policy.
- Include proper proxy headers (X-Real-IP, X-Forwarded-For).
- For WebSocket services, include proper upgrade headers (Connection, Upgrade).
- Output ONLY nginx config, no markdown wrappers.
"""

    try:
        response, attempts_used, fallback_used = invoke_with_retry(
            invoke_fn=lambda raw_prompt: llm_nginx.invoke(raw_prompt),
            prompt=prompt,
            fallback_prompt=FALLBACK_PROMPTS["nginx"],
            config=RETRY_CONFIGS["nginx"],
            node_name="nginx_gen",
        )
        nginx_conf = strip_markdown_wrapper(response.content, lang="nginx")
        if nginx_conf.startswith("conf\n"):
            nginx_conf = nginx_conf[5:]
        state["nginx_conf"] = nginx_conf
        state["nginx_retry_attempts"] = attempts_used
        state["nginx_fallback_used"] = fallback_used
    except Exception as e:
        state["error"] = f"Failed generating nginx.conf: {e}"
        return state
    
    return state
