# Quartermaster — Home Assistant integration

Custom Home Assistant integration (Marketplace, domain `quartermaster`): an AI agent for household inventory by zone, essentials and the shopping list. The dashboard (strategy and cards) lives in the sibling repository `HomeAssistant-Quartermaster-Dashboard`.

This file is shared with every contributor and agent. Keep it short, factual and free of personal or machine-specific content.

## Where to read

- `docs/dev/MEMORY.md`: where the work stands and what comes next. Read it at the start of every session; update it at the end.
- `docs/SPEC.md`: the binding specification. Read the section for the behaviour before changing it.
- `TECHNICAL_NOTES.md`: the current decisions and every deviation from the spec, by topic. Read the topic before changing that area or proposing a design.
- GitHub issues: open work and known bugs (see Agent skills).
- `CONTRIBUTING.md`: machine setup, dev instance, Cloudflare gateway patch.

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
- `docs/`: spec, user docs, dashboard API contract, `dev/`; `examples/`: automations, Alexa models, Phase 0 probes.
- `scripts/`: lint, dev tools, `alexa-models` (generates the Alexa models; a test checks they are current).

## Rules

- Follow the current official documentation (contributors.home-assistant.io, home-assistant.io, Marketplace docs, Alexa Skills Kit, Cloudflare), not memory. Where the docs are silent, stop, ask the author, then record the decision in `TECHNICAL_NOTES.md`.
- Use only documented Home Assistant APIs and the Python packages Home Assistant ships; anything else needs the author's agreement and a note in `TECHNICAL_NOTES.md`.
- Typed, async code with tests (a bug fix comes with its regression test); blocking work (SQLite, Pillow) runs in the executor. Per-entry objects live in `entry.runtime_data`.
- Everything user-facing is multilingual (at least English and Italian), Alexa included. Strings, exceptions included, go through translation keys in `translations/en.json` (complete) and `translations/it.json`; there is no `strings.json`.
- LLM tools are named `quartermaster__<name>`, return `llm.ToolResult`, set `integration` and declare `llm.ToolAnnotations`.
- Code, comments, commits and docs in English (`docs/SPEC.md` is the author's, in Italian).
- Every user-visible change goes into `CHANGELOG.md` under `[Unreleased]`; a changed `unique_id` is a breaking change and says so. Every deviation from the spec goes into `TECHNICAL_NOTES.md`.
- Code, examples, fixtures and docs use invented data only; personal environment notes go to `*.local.md` (gitignored).
- Develop and test on the dev instance. The author's real instance is read-only, and only when the author asks.
- Commit with debug flags off.
- Use the latest stable version of everything: Home Assistant (minimum = latest stable), libraries, GitHub Actions, Docker images, third-party integrations. Adopt new majors; pin back only to match Home Assistant's own manifests.
- Work on `main`. The pre-commit hook (`.githooks/pre-commit`, enabled with `git config core.hooksPath .githooks`) runs `ruff format --check`, `ruff check` and mypy; fix its failures with `bash scripts/lint`. Run the tests before committing. After every push, check all GitHub Actions runs of the commit and fix any failure at once.

## Decisions (do not re-litigate without a new reason)

- Minimum Home Assistant 2026.11.0 (Python ≥ 3.14.2), set in `hacs.json`.
- SQLite via the standard library; AI through `ai_task.generate_data` and the user's conversation agent; no provider clients.
- Frontend only from the dashboard repository; the integration ships no JavaScript.
- Action buttons only on Telegram and in the dashboard; other notify entities get plain text.

## Release

Bumping `version` in `custom_components/quartermaster/manifest.json` on `main` runs `tag-and-release.yaml`: tests and security checks, then the tag `v<version>`, the ZIP and the GitHub release from the matching `CHANGELOG.md` section. `0.0.0` is the unreleased placeholder and is never published. Bump the version, tag or release only when the author asks. Release in pairs with the dashboard.

## Agent skills

### Issue tracker

Issues are tracked in GitHub Issues (`gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

The five default triage labels (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` and `docs/adr/` at the repo root (created when needed), plus `TECHNICAL_NOTES.md`. See `docs/agents/domain.md`.
