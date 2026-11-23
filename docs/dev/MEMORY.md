# Working memory

Where the work stands, shared between sessions and machines. Rules live in `CLAUDE.md`, decisions in `TECHNICAL_NOTES.md`, open work in GitHub issues; this file holds only the current state and the next step. Keep it short, factual and free of secrets or personal data. Rewrite "Now" at the end of every working session and push it.

## Now (2026-11-23)

- Everything is on `main` and pushed in both repositories. No release yet (`0.0.0`).
- 2026-11-23: #24 (`zone_not_found` carries the zones and aliases) and #16 (entities check `write_users`, error `write_not_allowed`) done and closed. The `start_shopping` action is still not permission-checked (accepted for the dashboard, see TECHNICAL_NOTES "Configuration").
- 2026-11-15/16: undo by voice on Alexa; zone named inside a Telegram photo caption; every stocked product a photo does not show is proposed as "not seen"; a late Alexa reply continues on Telegram (the "sì" typed there takes over the voice conversation); a zone analysis keeps `matched_product_id` only when the reading shares a word with the product. Dev gateway patched for answers streamed only as reasoning.
- #18 (photos and cameras): first fridge photos with gemma-4-26b on dev; recall on real photos is the open question for the image model (see the issue comments).
- Alexa timing on dev: gemma-4-26b needs 3–5 s per round, so tool questions get the 5 s acknowledgement and the late reply goes to Telegram (`telegram_notify` on). Dev zone Frigorifero has the alias "frigo".
- Postponed, still planned: a full review of both repositories (module by module against `docs/SPEC.md`, or `/code-review` against `6a673f8`, plus `security-audit`); start only when the author asks.
- Next: #4, #5 (`ready-for-agent`); #21 needs triage; field validation #18, #19 and #20 with the author.

## Machine setup

Follow `CONTRIBUTING.md`, "Working on another computer". A new machine gets a fresh dev instance: the author enters provider keys again in the dev UI. `docs/ENVIRONMENT.local.md` (gitignored) exists only where the author copied it: ask for details instead of guessing.

## Dev instance (gitignored `config/`)

Durable facts; check the instance itself (ha-mcp on :8086) for anything else.

- Quartermaster entry titled "Quartermaster" (entity ids `todo.casa_dev_…` from the first title), language it, zones Dispensa, Frigorifero, Freezer and Pozzetto (ledger), seeded products.
- Cloudflare AI Gateway entry (patched, see `scripts/dev-cloudflare-gateway`) with conversation agents and `ai_task.bench_*` subentries from the benchmarks; only the chosen models are in use, the rest can be deleted.
- `config/packages/quartermaster_alexa.yaml` = `examples/alexa/quartermaster_alexa.yaml` with the test skill id; HA's own Shopping list integration removed; reverse proxy trusted for 172.18.0.0/16 (UI setting).
- Alexa test skill "dispensa smart" (it-IT), Lambda in eu-west-1 (timeout 10 s); its `BASE_URL` and account-linking URIs point to the author's quick tunnel, which changes on every `cloudflared` restart.
- Telegram test bot connected (polling).
