# Phase 0 — practical checks

Checks run together by the author and the agent. Record every outcome in `TECHNICAL_NOTES.md` (section "Phase 0 — practical checks") and tick it here. Probes live in `examples/phase0/` and `scripts/phase0/`. They are for checks only and never ship in the integration.

Rules:
- Dev instance (`docker compose up -d`) unless stated otherwise.
- The author's real instance only where marked **REAL**, and only when the author says so.
- No tokens, chat IDs, skill IDs, entity names of the author's home or photos in the repository. Outputs go to `~/quartermaster-phase0/`.

Enable probes on the dev instance (the files are gitignored):

```bash
mkdir -p config/packages config/custom_components
cp examples/phase0/packages/<probe>.yaml config/packages/
docker compose restart homeassistant
```

## 1. Telegram (dev instance)

Prerequisites:
- A **test** bot from @BotFather, not the production one.
- Telegram bot set up from the UI with the **Polling** platform and your chat allowlisted (subentry).
- Outbound HTTPS from the container must work: on networks with TLS inspection it fails, so use another network or machine.

Steps:
1. Copy `qm_probe_telegram.yaml`. Replace `event.REPLACE_ME_update_event` with the bot's event entity, then restart.
2. Send to the bot, in order: a text, the command `/start`, a photo with caption `frigo`, a photo without caption, an image sent as a file (document), a text from a group the bot is in but that is not allowlisted, then tap the probe buttons.
3. Read the result: `docker compose logs homeassistant | grep quartermaster_phase0.telegram`.

Record:
- [ ] Event types and attributes seen on the **event entity**: `file_id`, `file_mime_type`, caption in `text`, `chat_id`, `user_id`.
- [ ] Whether the same events arrive on the **bus** (undocumented path), and any differences.
- [ ] `telegram_bot.download_file` response (`file_path`), the location and size of the file, and whether a photo arrives at its largest size.
- [ ] Button press: `telegram_callback` data and `answer_callback_query` working.
- [ ] Group message from an allowlisted user in a non-allowlisted group: accepted or not.
- [ ] Clean up: delete `config/telegram_bot/` downloads.

## 2. Alexa (dev instance through a Cloudflare quick tunnel, on the second machine)

Already checked by simulating requests on the dev instance (2026-11-07, see TECHNICAL_NOTES):
- the launch and session-end handlers keyed by the skill ID;
- an empty reprompt gives `shouldEndSession: true`, a non-empty one gives `false`;
- `language` passes through the per-locale intents;
- a slow stub takes 9 s.

Still to check with a real Echo:

Prerequisites:
- A quick tunnel to `http://localhost:8123` with its HTTPS URL.
- Custom skill in the Alexa developer console, with the it-IT locale and optionally en-US / en-GB.
- The forwarding Lambda and account linking as in home-assistant.io/integrations/alexa.intent/, pointed at the tunnel URL.

Steps:
1. Import `examples/phase0/alexa/interaction_model.<locale>.json` into each locale and build.
2. Copy `qm_probe_alexa.yaml` and replace `REPLACE_WITH_SKILL_ID`, then restart.
3. Speak the phrases below. After each one, read `grep quartermaster_phase0.alexa`.

| # | Phrase | Expected |
| --- | --- | --- |
| 1 | Console accepts invocation name "dispensa" (it-IT) | If not, use "dispensa di casa" |
| 2 | "Alexa, dispensa" | "Dimmi", microphone open |
| 3 | "Alexa, chiedi a dispensa se ho ancora il latte" | Slot text logged; check which words are missing |
| 4 | "Alexa, di' a dispensa di segnare il latte finito" | As above |
| 5 | "Alexa, apri dispensa e segna il latte finito" | As above |
| 6 | "Alexa, usa dispensa e ho finito il formaggio in frigo" | As above |
| 7 | "Alexa, chiedi a dispensa: fammi una domanda" | Question and microphone open; the answer arrives as a new request |
| 8 | "Alexa, chiedi a dispensa: ho finito il latte" | Session closes after the answer (empty reprompt) |
| 9 | "Alexa, chiedi a dispensa una risposta media" | Answer after 6 s, spoken |
| 10 | "Alexa, chiedi a dispensa una risposta lenta" | 9 s: what Alexa does past the 8 s limit |
| 11 | en-US / en-GB: "Alexa, ask pantry if I still have milk" | English stub answer |
| 12 | Model built with a bare `{query}` sample (temporary edit) | Does the console accept it, and is the slot text whole? |

Record:
- [ ] Results 1–12, including the exact slot text for 3–6 and 11.
- [ ] Whether a fixed short reprompt ("Altro?") is needed (spec §6.4 fallback).
- [ ] Clean up: remove the probe package; keep the skill for Phase 8 or delete it.

## 3. Camera — **REAL** (reads only, plus one write if authorized)

Prerequisites: a long-lived token of the real instance, in the environment only.

1. Freshness: run the read-only script, then move in front of the camera 5 times, at least 1 minute apart:
   ```bash
   HA_URL=<real URL> HA_TOKEN=<token> uv run --with aiohttp --with pillow \
     scripts/phase0/camera_freshness.py --camera <camera entity> --trigger <motion event entity> \
     --delays 0,1,3,5 --events 5 --battery <battery sensor, if any>
   ```
   - [ ] For each delay: status, fetch time, and whether the frame differs from the baseline and from +0 s. A stale frame means 0 bits changed.
   - [ ] Battery before and after.
2. `camera.record`: **a write**; only with the author's explicit consent at that moment.
   - [ ] Run `camera.record` with `filename: /media/quartermaster_phase0/clip.mp4`, `duration: 6`. Note the elapsed time, the error (`record_not_supported`, `already_recording`) or the clip length.
   - [ ] Delete the clip afterwards (Media → Local media).
3. Recognition: take 10 photos of 10 different products held up in the show area. Save them under `~/qm-photos-show/` with `labels.csv` and run the AI Task probe below on the dev instance.
   - [ ] Count of products recognized out of 10.
   - [ ] Decision on camera_show: active, or implemented but kept off on the author's instance.

## 4. iOS (dev instance through the quick tunnel)

1. Copy `examples/phase0/file-capture-card.js` to `config/www/qm-phase0/`.
2. Add `/local/qm-phase0/file-capture-card.js` as a resource of type module (Settings → Dashboards → ⋮ → Resources).
3. Add a `custom:qm-file-capture-test` card.
4. Open the dashboard in the Home Assistant iOS app, connected through the tunnel.
   - [ ] Input A (`capture="environment"`): camera opens directly, or a menu offers camera and library, or library only.
   - [ ] Input B: what is offered.
   - [ ] Same checks in Safari.

## 5. Marketplace (dev instance, network without TLS inspection)

1. Settings → Marketplace → custom repositories: add `samuelebistoletti/HomeAssistant-Quartermaster-Dashboard` as **Dashboard**.
2. Install it.
   - [ ] The resource `/local/community/HomeAssistant-Quartermaster-Dashboard/quartermaster-dashboard.js?v=...` is listed in Resources.
3. Settings → Dashboards → Add dashboard.
   - [ ] **Quartermaster** is listed under community dashboards.
   - [ ] The generated view shows the "under development" message in Italian and English, depending on the user language.
4. Temporarily remove the `frontend.extra_module_url` line from `config/configuration.yaml` so the bundle is not loaded twice.
5. Add the integration repository as **Integration** too and install it.
   - [ ] It installs and configures from the Marketplace copy, with no mounted folder. Temporarily remove the mount from `docker-compose.yml`.
6. Documented way for the integration to check that the dashboard resource is installed: none found in the official docs (TECHNICAL_NOTES). The README and config-flow notice apply.

## 6. Cloudflare AI Gateway (dev instance, network without TLS inspection)

Code review of `michaeldwan/ha-cloudflare-ai-gateway` v2026.04.0: **does not load its agent and AI Task entities on HA 2026.11** (see TECHNICAL_NOTES). Decide first whether to:
- patch a local copy;
- wait for an upstream fix;
- use another route, such as OpenRouter.

Once a working route exists:
1. Tools:
   - copy `examples/phase0/quartermaster_probe/` to `config/custom_components/` and `qm_probe_llm.yaml` to `config/packages/`;
   - attach the **Assist** LLM API to the agent (2026.11: an admin must attach it);
   - ask in Assist, in Italian and in English: "Quanto latte ho?" / "How much coffee do I have?".
   - [ ] The log shows `probe tool called`, and the answers say 2 bottles of milk and 3 packs of coffee.
2. Structured output and images:
   ```bash
   HA_URL=http://localhost:8123 HA_TOKEN=$(cat config/.dev/ha_token) \
     uv run --with aiohttp scripts/phase0/ai_task_probe.py --entity <ai_task entity> --photos ~/qm-photos
   ```
   - [ ] Nested `structure` accepted and valid; or flat mode only.
   - [ ] Multiple attachments accepted.
   - [ ] Recall per photo.
   - [ ] Answer time.
3. Repeat with each candidate model:
   - glm-4.7-flash and gemma-4-26b as the agent;
   - gemma-4-26b and mistral-small-3.1 for images;
   - gpt-oss-20b for text.
4. Neurons: read the daily usage in the Cloudflare dashboard (Workers AI) before and after a typical session and compare it with the 10,000 neurons per day free allocation.
   - [ ] Italian answer quality per model.
   - [ ] Neurons per session.
5. Clean up: delete `config/media/quartermaster_phase0/` and remove the probe component and package.
