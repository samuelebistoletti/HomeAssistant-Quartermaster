# Contributing Guidelines

Bug reports, fixes and feature proposals are welcome.

## Pull Requests

1. Fork the repository and branch from `main`.
2. Update the documentation and both translation files (`translations/en.json`, `translations/it.json`) when you change behavior or user-facing text.
3. Run the linter and the type checker: `bash scripts/lint` and `mypy`.
4. Run the tests: `python -m pytest`.
5. Open the pull request.

Contributions are under the project's [MIT License](LICENSE).

## Reporting Bugs

Use [GitHub Issues](../../issues/new/choose). A good report has a short summary, steps to reproduce, what you expected, what happened and the relevant logs. Remove personal data (names, chat IDs, addresses, photos) before posting.

## Code Style

[Ruff](https://github.com/astral-sh/ruff) is the only formatter and linter (`bash scripts/lint`); mypy runs in strict mode. Code, comments and docstrings are in English; every user-facing string goes through translation keys.

## Testing

Tests use [pytest-homeassistant](https://pypi.org/project/pytest-homeassistant/), the official test package published from home-assistant/core, on Python 3.14.2 or later. Its version matches the Home Assistant version it tests against.

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt
python -m pytest
```

## Local Development Environment

### Prerequisites

- Git, and both repositories cloned side by side:

  ```bash
  git clone https://github.com/samuelebistoletti/HomeAssistant-Quartermaster.git
  git clone https://github.com/samuelebistoletti/HomeAssistant-Quartermaster-Dashboard.git
  ```

- Docker with Compose (Docker Desktop, OrbStack or Docker Engine). The images are published for amd64 and arm64.
- Python 3.14.2 or later, and [uv](https://docs.astral.sh/uv/) for `scripts/dev-bootstrap` from the host.
- Node.js 26 (the dashboard's `.nvmrc`) to build the dashboard.
- Optional: VS Code with the Dev Containers extension, which replaces the Python and uv steps.

### First start

```bash
# Dashboard bundle loaded by the dev instance
(cd ../HomeAssistant-Quartermaster-Dashboard && npm ci && npm run build)

# Tests, lint and type check on the host
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt

# Home Assistant on http://localhost:8123 and ha-mcp on http://localhost:8086
docker compose up -d

# First start only: onboard a throwaway dev user and create the ha-mcp token
uv run --with aiohttp scripts/dev-bootstrap
```

With VS Code, **Reopen in Container** does the same: it starts Home Assistant Core and the [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) MCP server next to the dev container, installs the dependencies and runs `scripts/dev-bootstrap`. Build the dashboard first.

### Daily work

- `custom_components/quartermaster` is mounted read-only: restart Home Assistant to load code changes (`docker compose restart homeassistant`). Logs: `docker compose logs -f homeassistant`.
- After changing the dashboard, run `npm run build` there and reload the browser.
- `scripts/dev-bootstrap` stores the dev login, the long-lived token and the ha-mcp endpoint path in `config/.dev/` (gitignored) and prints the ha-mcp URL. Add it to your MCP client, for example `claude mcp add --transport http --scope local ha-dev <url>`.
- Both ports are published on `127.0.0.1` only.
- `config/packages/` (gitignored) loads local YAML packages, such as the Phase 0 probes from `examples/phase0/packages/`.

### Cloudflare AI Gateway on the dev instance

The community [Cloudflare AI Gateway](https://github.com/michaeldwan/ha-cloudflare-ai-gateway) integration does not load on Home Assistant 2026.11 yet. `scripts/dev-cloudflare-gateway` downloads its latest version, applies the schema fix described in `TECHNICAL_NOTES.md` while upstream still needs it, and installs it in `config/custom_components/`:

```bash
scripts/dev-cloudflare-gateway && docker compose restart homeassistant
```

Then add it from **Settings** → **Devices & services** with your own account ID, gateway and token.

### Working on another computer

Everything needed is in the two repositories: follow [Prerequisites](#prerequisites) and [First start](#first-start), then run `scripts/dev-cloudflare-gateway` if you use that provider. A new computer gets a fresh dev instance. What is not in git stays on the old computer:

- `config/` except `configuration.yaml`: the dev instance's users, integrations (AI providers and their keys, Telegram), Quartermaster data and the ha-mcp token. Set them up again, or copy the folder with Home Assistant stopped if you want the same state. It holds secrets: never commit it or share it.
- `*.local.md` files with personal environment notes.
- Your MCP client registration of ha-mcp: run `claude mcp add` again with the URL that `scripts/dev-bootstrap` prints.
