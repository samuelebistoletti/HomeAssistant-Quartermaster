# Working memory

Where the work stands, shared between sessions and machines. Rules live in `CLAUDE.md`, decisions in `TECHNICAL_NOTES.md`, open work in GitHub issues; this file holds only the current state and the next step. Keep it short, factual and free of secrets or personal data. Rewrite "Now" at the end of every working session and push it.

## Now (2026-11-15)

- Everything is on `main` and pushed in both repositories. No release yet (`0.0.0`).
- 2026-11-15 with the author: #17 closed (domain `quartermaster` kept for 1.0); #16 decided (entities check `write_users`), now `ready-for-agent`. The author's test Lambda now has the `qm_device` lines (verified: the slot reaches HA); docs/alexa.md fixed, the lines go before `response = http.request(`, not before the `/api/alexa` line inside the call.
- Alexa timing on dev: gemma-4-26b needs 3–5 s per round, so any tool question takes ≥ 9 s and Alexa always gets the 5 s acknowledgement. Late voice replies on dev go to `notify.samuele_bistoletti`. Dev zone Frigorifero got the alias "frigo" (without it: 4 rounds, 16 s). #24 opened: `zone_not_found` lists the zones so the agent skips `list_zones`.
- Earlier work (2026-11-11/13): issues #1–#3, #6–#15, dashboard #2–#4. Not yet tried: photo, receipt and camera flows with the test bot and real cameras; the dashboard changes of 2026-11-12/13 in a browser. Dev product "Prova ciclo" left archived.
- Postponed, still planned: a full review of both repositories (module by module against `docs/SPEC.md`, or `/code-review` against `6a673f8`, plus `security-audit`); start only when the author asks.
- Next: #24, #16, #4, #5 (`ready-for-agent`); field validation #18, #19 and #20 with the author.

## Machine setup

Follow `CONTRIBUTING.md`, "Working on another computer". A new machine gets a fresh dev instance: the author enters provider keys again in the dev UI. `docs/ENVIRONMENT.local.md` (gitignored) exists only where the author copied it: ask for details instead of guessing.

## Dev instance (gitignored `config/`)

Durable facts; check the instance itself (ha-mcp on :8086) for anything else.

- Quartermaster entry titled "Quartermaster" (entity ids `todo.casa_dev_…` from the first title), language it, zones Dispensa, Frigorifero, Freezer and Pozzetto (ledger), seeded products.
- Cloudflare AI Gateway entry (patched, see `scripts/dev-cloudflare-gateway`) with conversation agents and `ai_task.bench_*` subentries from the benchmarks; only the chosen models are in use, the rest can be deleted.
- `config/packages/quartermaster_alexa.yaml` = `examples/alexa/quartermaster_alexa.yaml` with the test skill id; HA's own Shopping list integration removed; reverse proxy trusted for 172.18.0.0/16 (UI setting).
- Alexa test skill "dispensa smart" (it-IT), Lambda in eu-west-1 (timeout 10 s); its `BASE_URL` and account-linking URIs point to the author's quick tunnel, which changes on every `cloudflared` restart.
- Telegram test bot connected (polling).
