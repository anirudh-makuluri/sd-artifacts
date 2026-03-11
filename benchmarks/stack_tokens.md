# Stack Token Reference

This file documents canonical stack tokens used by the scanner and benchmark labels.

The single source of truth in code is `tools/stack_tokens.py`.

## Canonical Tokens

### Frontend / Web Frameworks

- `next`
- `nuxt`
- `react`
- `vue`
- `svelte`
- `angular`
- `remix`
- `astro`
- `gatsby`
- `vite`

### Backend / Runtime Frameworks

- `express`
- `nestjs`
- `fastify`
- `koa`
- `hapi`
- `fastapi`
- `flask`
- `django`
- `uvicorn`
- `gunicorn`
- `streamlit`
- `gradio`

### Languages / Platforms

- `node`
- `python`
- `go`
- `java`
- `dotnet`
- `aspnetcore`
- `bun`
- `ruby`
- `php`
- `rust`

### Data / Infra / Ecosystem

- `postgres`
- `mysql`
- `mongodb`
- `redis`
- `nginx`
- `apollo`
- `graphql`
- `prisma`
- `typeorm`
- `sequelize`
- `mongoose`
- `sqlalchemy`

## Alias Normalization

These aliases are normalized into canonical tokens:

- `nextjs` -> `next`
- `golang` -> `go`

## How Tokens Are Used

- Classification: identify app family and deployment shape.
- Artifact decisions: influence Dockerfile, compose, and nginx strategy.
- Port inference: framework defaults when explicit ports are missing.
- Benchmark scoring: `required_stack_tokens` checks scan correctness.

## Labeling Guidance

- Use canonical tokens in `required_stack_tokens`.
- Keep `required_stack_tokens` minimal and high-signal.
- Prefer framework/runtime tokens over broad tokens when possible.
