# Working memory

Shared state between development sessions and machines. Keep it short and factual, and free of secrets or personal data. Durable architecture decisions go to `TECHNICAL_NOTES.md`; this file tracks where the work stands. Update it and push it at the end of every working session.

## Resume here (last update 2026-11-11)

0. **Assessment done on 2026-11-11** (`docs/dev/ASSESSMENT-2026-11-11.md`): twelve groups of bugs fixed in both repositories with regression tests; the report lists what is still open, the project gaps (A–E) and the order. Implemented on 2026-11-11 (see TECHNICAL_NOTES, "Cameras always ask; zones kept by word"): cameras never write, and the `ledger` source with the guided check. Spec (§4.1, §5, §5.1, new §5.2), README, user guide and dashboard API updated. Dev instance: zone Pozzetto (ledger) with two products; its guided check was sent to the test bot chat by simulating `/controlla pozzetto` on the event entity (2026-11-11, 15:13 UTC). Still to do: the author's taps on that message; a camera_show trial; the "Not done yet" row of that section. Nothing of 2026-11-11 is pushed: the author wants the agent benchmark first, and Workers AI still answered 429 (internal code 4006, "daily free allocation used up") at 15:11 UTC, also called directly outside the gateway, although the dashboard showed 0/10k and the docs say the allocation resets at 00:00 UTC: a Cloudflare-side block to raise with the author.

1. Read `CLAUDE.md` (rules), this file, then `TECHNICAL_NOTES.md` and `docs/SPEC.md` as needed. The author writes in Italian; answer in Italian, code and docs in English.
2. Set up the machine as in `CONTRIBUTING.md` → "Working on another computer" (both repos side by side, dashboard built, `.venv`, `docker compose up -d`, `scripts/dev-bootstrap`, then `scripts/dev-cloudflare-gateway`). A new machine has a fresh dev instance: the author enters provider keys again in the dev UI. `docs/ENVIRONMENT.local.md` (author's environment, gitignored) exists only where the author copied it: ask for the details you need instead of guessing.
3. Current step: **choose the voice agent among free Workers AI models.**
   - Run `scripts/dev-agent-bench` (13 phrases, stock checks, waits for deferred replies) on `conversation.gemma_4_26b_agent`, `conversation.llama_3_3_70b_fast`, `conversation.phonellm_mini` (already created on the dev gateway entry, Assist attached). The 2026-11-10 run failed only because the daily 10,000 neurons were used up (resets 00:00 UTC). Also read tokens per call from the gateway logs (`/ai-gateway/gateways/<gw>/logs`, user token from the gateway config entry; never print it).
   - 2026-11-11, 07:21 UTC: run again, every call refused with the same 429 (daily 10,000 neurons used up) although the gateway logs show no successful call since the evening before. Either the allocation does not reset at 00:00 UTC as assumed, or something outside the gateway uses the account's neurons: check the Workers AI usage in the Cloudflare dashboard before the next run. The script no longer drops the AI Task options when it changes the agent (they were restored on the dev entry).
   - Check with the new prompt rule that "segna i biscotti nella lista della spesa" lands on Quartermaster's list.
   - Not chosen yet: gpt-oss-120b needs a patch to the community integration (string `content`, `content` on assistant tool-call messages); Claude Haiku through the official Anthropic integration is the paid option (author asked to stay free for now).
   - Then: Telegram texts and photos through Quartermaster (channel already connected to the test bot; commands work), Alexa voice re-test with the per-carrier-word models (`examples/alexa`, regenerate with `scripts/alexa-models`; the author must paste the it-IT JSON into the console), Phase 4 recall on ≥30 author photos (target 85 %), threshold tuning.
4. After that, with the author: cameras on the real instance (read-only, only when asked), iOS through the tunnel, English locale on the Echo, Marketplace check, provider table in the README. Release 1.0.0 only when the author asks.

### Dev instance state (2026-11-10, gitignored `config/`)

- Quartermaster entry titled "Quartermaster" (entity ids still `todo.casa_dev_…` from the first title), language it, zones Dispensa/Frigorifero/Freezer, a few seeded products; agent option `conversation.llama_4_scout`, image and text AI Task `ai_task.bench_llama_4_scout_17b_16e_instruct`.
- Cloudflare AI Gateway entry with many `ai_task.bench_*` subentries from the benchmark (only the llama-4-scout one is used; the rest can be deleted) and conversation agents glm-4.7-flash, llama-4-scout, granite-micro, gpt-oss-120b, gemma-4-26b, llama-3.3-70b-fast, phonellm-mini.
- `config/packages/quartermaster_alexa.yaml` = `examples/alexa/quartermaster_alexa.yaml` with the test skill ID; the Alexa probe is parked as `config/qm_probe_alexa.yaml.off`. HA's own Shopping list integration removed. HTTP reverse proxy trusted for 172.18.0.0/16 (UI setting).
- Alexa test skill "dispensa smart" (it-IT) with the Lambda in eu-west-1 (timeout 10 s); its `BASE_URL` and the account-linking URIs point to the author's current quick tunnel, which changes on every `cloudflared` restart.

## Decisions from the author

- Spec committed without personal or environment data; personal notes in `*.local.md` (gitignored).
- Work directly on `main`; pushing is allowed. No version bump, tags or releases unless asked. Release tags use the `v` prefix.
- Always the latest stable version of everything; Home Assistant minimum = latest stable (2026.11.0), older releases not supported. Adopt new majors. Exception: pins that must match Home Assistant's manifests (`gazetteer-matcher`).
- After every push wait for all GitHub Actions runs and fix failures; run `ruff format --check` before committing.
- Domain stays `quartermaster`; the README does not mention the other integration using it.
- Everything user-facing multilingual (en, it), Alexa included; one multi-locale skill.
- Action buttons only on Telegram and in the dashboard.
- Cameras: off by default; zone + passage cameras; positive-only corroboration; passage cameras only propose.
- Receipts and order emails always become a proposal; the forecast also puts essentials on the list.
- Permissions: administrators plus a chosen list of non-admin users (`write_users`), listed with `hass.auth.async_get_users()` (public, not in the developer docs; accepted by the author).
- The agent's tools and `quartermaster.ask` follow `write_users` (2026-11-11).
- Fridge, freezer and chest freezer have no cameras: the author keeps them by voice (Alexa) and Telegram (2026-11-11).
- Every camera action must be confirmed by the user on Telegram before the stock changes, removals included. Goods enter with a confirmed receipt or with a photo sent on purpose (gifts, purchases without receipt); cameras are for what goes out and for the state of a shelf and never load anything; a camera seeing goods placed only confirms a receipt; no placement allowance; `confirm: auto` goes away (2026-11-11; design in the assessment, section E, not implemented yet).
- A photo request started from a Telegram chat goes back to that chat (chat id from the conversation session).
- Kept as they are: no debug retention of photos; analyses tile instead of AI cost; dashboard reload after adding or deleting a zone.
- Cloudflare AI Gateway: test now with the locally patched community integration (dev only), not waiting for upstream.
- Ring camera checks only on the real instance, read-only, when the author says so. Alexa and iOS through a Cloudflare quick tunnel on the second machine.
- Python-side translated phrases via `async_get_translations`; action `config_entry_id` optional (documented deviations).

## Open questions for the author

- Marketplace check needs the author's GitHub device sign-in on the dev instance; and if the Marketplace requires a release, whether a test pre-release is allowed.
- Assessment section F (zones kept by voice and Telegram only, such as the chest freezer: `tracking: ledger` with a guided check on Telegram) is proposed, waiting for the author's answer.
- Whether the entities (todo, buttons, select, switch) should check `write_users` or keep the documented limit; whether to keep the domain `quartermaster` given the other integration using it.
