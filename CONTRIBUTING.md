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

The easiest path is the VS Code dev container: **Reopen in Container** starts Home Assistant Core and the [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) MCP server next to the dev container, installs the dependencies and runs `scripts/dev-bootstrap`.

Without VS Code:

```bash
# Home Assistant on http://localhost:8123 and ha-mcp on http://localhost:8086
docker compose up -d

# First start only: onboard a throwaway dev user and create the ha-mcp token
uv run --with aiohttp scripts/dev-bootstrap

docker compose logs -f homeassistant
```

- `custom_components/quartermaster` is mounted read-only: restart Home Assistant to load code changes (`docker compose restart homeassistant`).
- The dashboard repository is expected next to this one. Its `dist/` is loaded by the dev instance; run `npm run build` there.
- `scripts/dev-bootstrap` stores the dev login, the long-lived token and the ha-mcp endpoint path in `config/.dev/` (gitignored) and prints the ha-mcp URL. Add it to your MCP client, for example `claude mcp add --transport http --scope local ha-dev <url>`.
- Both ports are published on `127.0.0.1` only.
