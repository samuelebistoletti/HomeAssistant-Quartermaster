# Quartermaster — Technical Notes

Deep architecture notes and every decision taken during development, including each deviation from `docs/SPEC.md`. Read before changing storage, the agent tools, channels or entities.

## Phase 0 — documentation review (2026-11-07)

Spec sections 12 (Phase 0.1) and 17 re-checked against the current official sources. Note: `developers.home-assistant.io` now redirects to `contributors.home-assistant.io`. Status: **R** = reported to the author, decision pending; **A** = adopted (documentation is binding).

### Deviations from the spec

| # | Spec | Official source | Consequence | Status |
| --- | --- | --- | --- | --- |
| 1 | §6.1 tool names `QuartermasterListZones`, ... | Tools contributed through `llm.py` must be named `<domain>__<name>`; unprefixed names are logged and stop working in 2027.3 (contributors.home-assistant.io/core/internals/llm-api/) | Tools are named `quartermaster__list_zones`, `quartermaster__get_stock`, ... | A |
| 2 | §17.1 `ai_task.generate_data` page shows `media-source://camera/<entity_id>` | The example is on the integration page (home-assistant.io/integrations/ai_task/), not the action page | Citation only | A |
| 3 | §9.3 PRs to home-assistant/brands | Since 2026.3 a custom integration ships `brand/` in its folder; local images take precedence; the catalog `brands` check passes with it (contributors.home-assistant.io/core/integrations/brands/) | No brands PR. `brand/icon.png` 256×256, `icon@2x.png` 512×512, `logo.png` / `logo@2x.png` (shortest side 128–256 / 256–512), optional `dark_` variants | A |
| 4 | §9.1 / §11 tag equal to `version` | "use the same version in `manifest.json` as in the tag"; a `v` prefix is not documented (contributors.home-assistant.io/marketplace/guides/releases) | Tags without `v` (e.g. `1.0.0`), unlike OctopusEnergyIT | A |
| 5 | §17.2 panels from Python undocumented | `panel_custom.async_register_panel()` and `hass.http.async_register_static_paths` are documented; `add_extra_js_url` is not | Wording only: the decision not to ship frontend code from the integration stands | A |
| 6 | §6.4 interaction model: utterance `"{query}"` | "Each sample utterance must include a carrier phrase" (ASK slot-type-reference, AMAZON.SearchQuery) | Carrier-phrase utterances (e.g. "se {query}", "di {query}", "che {query}", "ho {query}"); Phase 0 checks the slot text arrives whole | A |
| 7 | §6.4 example `intent_script` handles only StopIntent | Unhandled intents answer "This intent is not yet configured within Home Assistant" (core `alexa/intent.py`, source) | Example also handles `AMAZON.CancelIntent` and `<skill-id>.SessionEndedRequest` | A |
| 8 | §6.4 one-word invocation name refused only at certification | it-IT rules: "One-word invocation names are not allowed" with no development/certification distinction (ASK choose-the-invocation-name) | Expect "dispensa di casa"; Phase 0 still tries "dispensa" in the console | A |
| 9 | §5.1 / §6.9 notification with an "Annulla" button via notify entities | `notify.send_message` has only `message` and `title` (home-assistant.io/actions/notify.send_message/) | Buttons need `telegram_bot.send_message` with `inline_keyboard` or companion-app actionable notifications | R |
| 10 | §10 / §11 minimum HA version | ToolResult, `integration` and annotations from 2026.10 (developer blog 2026-09-26); `pytest-homeassistant` first aligned release 2026.11.0 (PyPI); Marketplace 2026.11 | Proposed minimum **2026.11.0**, Python ≥ 3.14.2 | R |
| 11 | §11 README also covers installation from HACS on older versions | With a 2026.11 minimum no supported version lacks the Marketplace; separate HACS cannot be installed on 2026.11 (release 2026.11 notes) | Drop the legacy-HACS section | R |
| 12 | §17.1 probatio: new code imports probatio | Allowed, not required ("Custom integrations need no changes"); LLM tool `parameters` are probatio schemas | Import `probatio` in new code, as the spec says | A |
| 13 | §17.1 quality scale "recommended for custom" | Page covers core only | Bronze remains a project goal, not a documented requirement | A |
| 14 | §17.1 Cloudflare `/compat` still works, REST API preferred | `/compat` "is deprecated" for single-model chat completions but "will continue to work"; new gateways require authentication | Only affects the author's provider choice and the community integration review | A |

### Facts not in the spec (to apply in later phases)

- **2026.11, LLM APIs:** "Any LLM API becomes available only once an administrator attaches it to a conversation agent or to the MCP server". The README and config flow must say so.
- **2026.11, conversations:** "an AI conversation can now only be continued by the user who started it", and one message per conversation is processed at a time. `quartermaster.ask` and the Telegram channel must keep the same user context per session. Verify in Phase 0.
- **2026.11, AI Task permissions:** changelog #185004 "AI task generation permissions". The effect on calls from an integration's own context is not documented. Verify in Phase 0.
- **LLM tools:** `llm.ToolAnnotations(read_only, destructive, idempotent, open_world)`; the defaults assume the worst. The MCP server can expose only read-only tools. `LLMContext` carries `platform`, `context` (user), `language`, `assistant`, `device_id`.
- **AI Task:** multiple attachments and video are not documented, so the §17.2 fallback stays. `structure` is converted into a probatio schema.
- **Manifest:** `iot_class` is required; `calculated` ("Does not talk to anything") is the closest documented class. `single_config_entry` is available.
- **Telegram:**
  - Event entity `event.<bot>_update_event` with `event_type` among `telegram_text`, `telegram_command`, `telegram_callback`, `telegram_attachment`, `telegram_sent`. Only the entity is documented; core source also fires bus events.
  - Photos: top-level `file_id` (largest size).
  - `telegram_bot.download_file` returns `file_path`; since 2026.10 a custom `directory_path` must be in `allowlist_external_dirs`.
  - Only the Polling and Webhooks platforms receive messages.
  - Core authorizes a message if the user ID *or* the chat ID is allowlisted, so Quartermaster's own group filter (§8) is required.
  - Legacy `notify.telegram` was removed in 2026.11.
- **Alexa:**
  - Reprompt is optional in the `intent_script` schema but missing from its docs page. An empty rendered reprompt leaves `shouldEndSession: true` (core source).
  - A LaunchRequest maps to the skill ID; a SessionEndedRequest maps to `<skill-id>.SessionEndedRequest`.
  - A slot without a value leaves its variable undefined.
  - Echo Show devices may keep the screen session open about 30 s.
  - Lambda regions documented for UK/DE/ES/FR (EU Ireland); Italian is not listed.
- **camera.record:**
  - Blocks for about `duration` seconds.
  - Raises `record_not_supported` without a stream and `already_recording` on a concurrent call.
  - `lookback` needs an active HLS stream.
- **Ring:**
  - `live_view` and `last_recording` cameras, the latter needing Ring Protect and disabled by default.
  - Doorbells and stickup cameras only.
  - Cloud-only, polled every 60 s; real-time events need outbound TCP 5228.
- **Alexa Devices:** Speak and Announce notify entities, rate-limited by Amazon.
- **Marketplace:**
  - Checks: archived, brands, description, hacsjson, images, information, integration_categories (1–3), issues, topics. `ignore` is not accepted by the catalog.
  - Install-time checks: "Loadable manifest" and "Module present".
  - Dashboard repos are category `plugin` (UI label "Dashboard").
  - The JS file is looked up in latest-release assets, then the repository root, then `dist`. It is registered as a `module` resource at `/local/community/<repo>/<file>?v=...`, unless resources are in YAML mode.
- **hacs.json keys:** name, content_in_root, zip_release, filename, hide_default_branch, country (unused), homeassistant, hacs (not compared), persistent_directory. `render_readme` is undocumented.
- **Dashboard resource check from the integration:** no documented websocket or REST API (no `lovelace/*` in the websocket API page; `hacs/*` commands are gone). The §6.5 fallback applies: README and config flow notice.
- **Strategies:** registering custom dashboard strategies requires 2026.5. Element tag `ll-strategy-dashboard-<name>`; strategies regenerate on registry changes, not on state changes.
- **Lit:** Rollup is the recommended production bundler.
- **Cloudflare:**
  - Workers AI: 10,000 free neurons/day; `gemma-4-26b-a4b-it` has vision, function calling and zero data retention; `glm-4.7-flash` has function calling, no vision.
  - None of the chosen models appears on the JSON Mode page, so the Phase 0 structured-output check is needed.
  - The community integration `michaeldwan/ha-cloudflare-ai-gateway` has tag `v2026.04.0` (latest) and requires `openai>=2.21.0`.

### Domain availability (Phase 0.b)

- `quartermaster` is absent from the hacs/default catalog (integration and plugin lists, HACS data by domain), from home-assistant/brands and from core.
- The custom integration `quartermaster-app/ha-quartermaster` (a shopping-list app, releases v0.2.0 and v0.2.1 on 2026-10-08) already uses the domain `quartermaster`. Both cannot be installed together, and the Marketplace rule "Two repositories can not install the same domain" may matter at catalog inclusion.
- The author kept the domain (2026-11-07). Mention the conflict in the README.
