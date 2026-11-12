# Quartermaster — Home Assistant integration

Custom Home Assistant integration (Marketplace/HACS, domain `quartermaster`): an AI agent for household inventory by zone, essentials and the shopping list. The binding specification is `docs/SPEC.md`; deviations and decisions are in `TECHNICAL_NOTES.md`; where the work stands is in `docs/dev/MEMORY.md`. Read all three before starting. The dashboard lives in the sibling repository `HomeAssistant-Quartermaster-Dashboard`.

This file is shared with every contributor and agent. Keep it short, factual and free of personal or machine-specific content.

## Commands

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest            # tests + coverage gate (90 %)
.venv/bin/mypy                        # strict type check
bash scripts/lint                     # ruff format + ruff check --fix (the only formatter/linter)
docker compose up -d                  # dev HA :8123 + ha-mcp :8086 (also started by the dev container)
uv run --with aiohttp scripts/dev-bootstrap  # first start: dev user + ha-mcp token in config/.dev/
scripts/dev-cloudflare-gateway               # optional: patched Cloudflare AI Gateway integration on the dev instance
```

## Layout

- `custom_components/quartermaster/`: the integration (`core/`, `storage/`, `vision/`, `channels/`, platforms, `llm.py`, `translations/`, `brand/`).
- `tests/`: pytest-homeassistant tests.
- `docs/`: spec, user docs, `dev/MEMORY.md`; `examples/`: automations and Alexa models.
- `.github/workflows/`: tests, security, hassfest, hacs, codeql, issues, tag-and-release.

## Rules

- Follow the current official documentation (contributors.home-assistant.io, home-assistant.io, Marketplace docs, Alexa Skills Kit, Cloudflare), not memory. If the docs do not cover a point, stop, ask the author, then record the decision in `TECHNICAL_NOTES.md`.
- No undocumented Home Assistant internals. No Python requirements beyond what Home Assistant ships, unless the author agrees.
- Typed, async code; blocking work (SQLite, Pillow) runs in the executor. Per-entry objects live in `entry.runtime_data`.
- Everything user-facing is multilingual (at least English and Italian), Alexa included. Strings, exceptions included, go through translation keys in `translations/en.json` (complete) and `translations/it.json`; no `strings.json`.
- LLM tools are named `quartermaster__<name>`, return `llm.ToolResult`, set `integration` and declare `llm.ToolAnnotations`.
- Code, comments and docs in English. Changing a `unique_id` is a breaking change: document it in `CHANGELOG.md`.
- No secrets, personal data or real home details in code, examples, fixtures or docs. Personal environment notes go to `*.local.md` (gitignored).
- Development and tests run on the dev instance only. On the author's real instance: reads only, and only when asked.
- No debug flags enabled in commits.
- Always the latest stable version of everything: Home Assistant (the minimum is the latest stable; older releases are not supported), libraries, GitHub Actions, Docker images, third-party integrations. Adopt new majors instead of pinning back; the only exception is a pin that must match Home Assistant's own manifests.
- Before committing run `.venv/bin/ruff format --check .` too; after every push wait for all GitHub Actions runs of the commit and fix any failure at once.

## Decisions (do not re-litigate without a new reason)

- Minimum Home Assistant 2026.11.0 (Python ≥ 3.14.2), in `manifest.json` and `hacs.json`.
- SQLite via the standard library; AI through `ai_task.generate_data` and the user's conversation agent; no provider clients.
- Frontend only from the dashboard repository (dashboard strategy + cards); the integration ships no JavaScript.
- Action buttons only on Telegram and in the dashboard; other notify entities get plain text.

## Release

Bumping `version` in `custom_components/quartermaster/manifest.json` on `main` runs `tag-and-release.yaml`: tests and security checks, then the tag `v<version>`, the ZIP and the GitHub release from the matching `CHANGELOG.md` section. `0.0.0` is the unreleased placeholder and is never published. Never bump the version, tag or release unless the author asks.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root (created when needed), plus `TECHNICAL_NOTES.md`. See `docs/agents/domain.md`.
