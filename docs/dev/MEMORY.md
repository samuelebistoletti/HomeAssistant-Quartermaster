# Working memory

Shared state between development sessions and machines. Keep it short and factual, and free of secrets or personal data. Durable architecture decisions go to `TECHNICAL_NOTES.md`; this file tracks where the work stands. Update it and push it at the end of every working session.

## Resume here (last update 2026-11-08)

1. Read `CLAUDE.md` (rules), this file, then `TECHNICAL_NOTES.md` and `docs/SPEC.md` as needed. The author writes in Italian; answer in Italian, code and docs in English.
2. Set up the machine as in `CONTRIBUTING.md` → "Working on another computer" (both repos side by side, dashboard built, `.venv`, `docker compose up -d`, `scripts/dev-bootstrap`, then `scripts/dev-cloudflare-gateway`). A new machine has a fresh dev instance: the author enters provider keys again in the dev UI. `docs/ENVIRONMENT.local.md` (author's environment, gitignored) exists only where the author copied it: ask for the details you need instead of guessing.
3. Current step: **real-provider tests on the dev instance through the Cloudflare AI Gateway.**
   - Waiting for the author to add the Cloudflare AI Gateway integration in the dev UI (account ID, gateway, token).
   - Then create a conversation agent, a text AI Task and an image AI Task with the author's chosen Workers AI models (agent `glm-4.7-flash`, text `gpt-oss-20b`, images `gemma-4-26b-a4b-it`), attach the Assist API to the agent, select them in Quartermaster's options.
   - Check first: gateway authentication (the author's gateway requires it; the integration's chat client sends the token only as `Authorization`, without `cf-aig-authorization`: if refused, add the header in `scripts/dev-cloudflare-gateway`), then whether Workers AI accepts `strict: true` structured output with optional fields (fallback: text structure mode).
   - Then: Phase 3 dialogs (ten phrases, it/en), receipts and order emails, Phase 4 recall on ≥30 author photos (target 85 %), threshold tuning.
4. After that, with the author: Telegram bot on dev, cameras on the real instance (read-only, only when asked), Alexa and iOS through a tunnel, Marketplace check, provider table in the README. Release 1.0.0 only when the author asks.

## Status

- Phases 0.a–0.e, 1–6, 8 and 9 implemented (no release); spec audit gaps closed; §15 implemented: receipts, order emails (IMAP), forecast, door-triggered interior cameras. Grocy/EverShelf dropped, ESPHome scales postponed (author).
- Guided multi-step config flow and language option.
- Integration: 203 tests, ~97 % coverage, ruff, mypy strict; CI green on `main`. Dashboard: 106 tests, TypeScript 7 (`tsc` 7.0, tools on `@typescript/typescript6`), CI green; Dependabot PR #1 closed after the move.
- Dev instance: Home Assistant 2026.11.0, ha-mcp 9.1.2, patched Cloudflare AI Gateway integration installed (dev only), loads cleanly.
- Alexa Phase 0 done on a real Echo with Alexa+ (2026-11-10): invocation "dispensa smart" / "smart pantry", per-locale yes/no intents, Lambda timeout 10 s; see TECHNICAL_NOTES. Open: `quartermaster.ask` fails with "conversation is busy" when a new request arrives while the agent is still answering; English locale not yet tried on the Echo.
- Pending real-world checks: Phase 0 practical checks (Telegram, iOS, camera freshness and clip mode, Marketplace, Cloudflare), real-provider dialogs and recall, camera tuning, Alexa end to end.

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
- A photo request started from a Telegram chat goes back to that chat (chat id from the conversation session).
- Kept as they are: no debug retention of photos; analyses tile instead of AI cost; dashboard reload after adding or deleting a zone.
- Cloudflare AI Gateway: test now with the locally patched community integration (dev only), not waiting for upstream.
- Ring camera checks only on the real instance, read-only, when the author says so. Alexa and iOS through a Cloudflare quick tunnel on the second machine.
- Python-side translated phrases via `async_get_translations`; action `config_entry_id` optional (documented deviations).

## Open questions for the author

- Marketplace check needs the author's GitHub device sign-in on the dev instance; and if the Marketplace requires a release, whether a test pre-release is allowed.
