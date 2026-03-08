# sd-artifacts — Planned Improvements

## [x] 1. Streaming Responses (SSE)
- Replace single POST response with Server-Sent Events
- Stream each node's output as it completes: scanner → planner → dockerfiles → compose → nginx → verifier
- Frontend gets real-time progress instead of waiting for the full pipeline
- Use FastAPI's `StreamingResponse` with `text/event-stream`

## 2. CI/CD Pipeline Generation
- New `cicd_generator_node` after the verifier
- Generate GitHub Actions workflow (`.github/workflows/deploy.yml`)
- Support GitLab CI (`.gitlab-ci.yml`) as an alternative
- Steps: build Docker images, push to registry, deploy to target (ECS, Cloud Run, k8s)
- Auto-detect existing CI configs and improve them (same pattern as Dockerfile handling)

## 3. Environment Variable Extraction
- New `env_extractor_node` that parses codebase for env var references
- Scan `process.env.*`, `os.getenv()`, `.env` files, and config files
- Generate a `.env.example` with all required variables, defaults, and descriptions
- Flag missing vars in compose and Dockerfiles

## 4. Cost Estimation
- Calculate `$` cost from token usage using model pricing
- Include `cost_usd` field in API response
- Support pricing for different models (Haiku, Sonnet, etc.)
- Track per-node cost breakdown for optimization

## [x] 6. Caching & Rate Limiting
- Cache repo scans by `repo_url + commit_sha` to avoid redundant GitHub API calls
- Use in-memory cache (TTL-based) or Redis for persistence
- Add rate limiting per API key / IP to prevent abuse
- Return cached results instantly when available

## 7. Deployment README Generation
- New `readme_generator_node` at the end of the pipeline
- Generate a `DEPLOY.md` with step-by-step deployment instructions
- Include: prerequisites, env setup, build commands, and common troubleshooting
- Tailored to the detected stack and generated configs

## [x] 8. Retry Logic & Error Recovery
- Add exponential backoff retries for failed LLM calls
- Retry on malformed output (Pydantic validation failures)
- Set per-node timeout limits
- Fallback to simpler prompts on repeated failures

## [x] 10. Example Bank Grounding (Supabase)
- Added `example_bank` table in Supabase with RLS + indexes for `artifact_type`, `quality_score`, and `stack_tags`.
- Added seeding pipeline (`tools/example_bank.py`) to ingest Dockerfile/compose examples from curated popular repos.
- Added permissive license filtering and upsert behavior keyed by `source_repo + source_path`.
- Added retrieval + ranking logic based on stack tags and quality score.
- Integrated retrieved examples into generation prompts:
  - `graph/nodes/dockerfile_generator.py`
  - `graph/nodes/compose_generator.py`
- Added API endpoints:
  - `POST /examples/seed`
  - `POST /examples/seed/popular`
  - `POST /examples/preview`
- Updated docs (`README.md`) with Supabase setup and example bank usage.

## 11. Impact Metrics for Grounded Generation
- Add run telemetry to measure whether `example_bank` grounding actually improves output quality over prompt-only generation.
- Add a Supabase table `analysis_runs` with fields:
  - `id`, `created_at`, `repo_url`, `commit_sha`, `package_path`
  - `strategy` (`prompt_only` | `grounded_examples`)
  - `detected_stack`, `services_count`
  - `token_input`, `token_output`, `token_total`
  - `latency_ms`, `example_count_used`, `example_retrieval_ms`
- Add a Supabase table `analysis_quality_metrics` keyed by `run_id` with fields:
  - `hadolint_warning_count`, `hadolint_error_count`
  - `risk_count`, `confidence`
  - `compose_valid`, `port_consistency_pass`
  - `non_root_services_count`, `healthcheck_services_count`, `multi_stage_services_count`
- Instrumentation points:
  - `app.py`: start/end timing, strategy label, token totals, persist `analysis_runs`
  - `graph/nodes/dockerfile_generator.py`: count retrieved examples per service
  - `graph/nodes/compose_generator.py`: count compose examples used
  - `graph/nodes/verifier.py`: convert hadolint output to numeric counters + compose validity checks
- Optional human feedback table `analysis_feedback`:
  - `run_id`, `accepted`, `manual_edits_required`, `manual_fix_minutes`, `notes`
- Report weekly KPIs:
  - Median hadolint warnings per run
  - Median verifier risk count
  - `% runs with confidence >= 0.85`
  - `% runs with compose valid + port consistency pass`
  - Median manual fix minutes
