# Working memory

Where the work stands, shared between sessions and machines. Rules live in `CLAUDE.md`, decisions in `TECHNICAL_NOTES.md`, open work in GitHub issues; this file holds only the current state and the next step. Keep it short, factual and free of secrets or personal data. Rewrite "Now" at the end of every working session and push it.

## Now (2026-11-12)

- Everything is on `main` and pushed in both repositories; CI green. No release yet (`0.0.0`).
- Done on 2026-11-11: the assessment fixes, cameras that only ask, the list check (`ledger` zones), "not seen → 0" rows, the model choice (gemma-4-26b agent and images, glm-4.7-flash text). The assessment report (`docs/dev/ASSESSMENT-2026-11-11.md`) is a frozen snapshot; its open points are now issues.
- Open work: GitHub issues in both repositories. `ready-for-agent` issues can be picked up; `ready-for-human` ones wait for the author (decisions and field tests).
- Next (agreed 2026-11-12): a full code review of both repositories in a fresh session (`/code-review ultra`, started by the author, or `/code-review` against the first commit, plus `security-audit`); findings become GitHub issues.
- Then, suggested order: integration #1 (unknown items as new products), #3 (placed goods vs receipts), #2 with dashboard #3 (product life cycle), dashboard #2 (movement requests), then the smaller fixes; field validation (#18, #19) whenever the author is available; #16 and #17 before 1.0.

## Machine setup

Follow `CONTRIBUTING.md`, "Working on another computer". A new machine gets a fresh dev instance: the author enters provider keys again in the dev UI. `docs/ENVIRONMENT.local.md` (gitignored) exists only where the author copied it: ask for details instead of guessing.

## Dev instance (gitignored `config/`)

Durable facts; check the instance itself (ha-mcp on :8086) for anything else.

- Quartermaster entry titled "Quartermaster" (entity ids `todo.casa_dev_…` from the first title), language it, zones Dispensa, Frigorifero, Freezer and Pozzetto (ledger), seeded products.
- Cloudflare AI Gateway entry (patched, see `scripts/dev-cloudflare-gateway`) with conversation agents and `ai_task.bench_*` subentries from the benchmarks; only the chosen models are in use, the rest can be deleted.
- `config/packages/quartermaster_alexa.yaml` = `examples/alexa/quartermaster_alexa.yaml` with the test skill id; HA's own Shopping list integration removed; reverse proxy trusted for 172.18.0.0/16 (UI setting).
- Alexa test skill "dispensa smart" (it-IT), Lambda in eu-west-1 (timeout 10 s); its `BASE_URL` and account-linking URIs point to the author's quick tunnel, which changes on every `cloudflared` restart.
- Telegram test bot connected (polling).
