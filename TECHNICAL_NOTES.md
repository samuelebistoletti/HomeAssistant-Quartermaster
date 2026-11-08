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
| 4 | §9.1 / §11 tag equal to `version` | "use the same version in `manifest.json` as in the tag"; a `v` prefix is not documented (contributors.home-assistant.io/marketplace/guides/releases) | Author decision (2026-11-07): tags keep the `v` prefix (`v1.0.0`) as in OctopusEnergyIT; manifest `version` without `v` | A |
| 5 | §17.2 panels from Python undocumented | `panel_custom.async_register_panel()` and `hass.http.async_register_static_paths` are documented; `add_extra_js_url` is not | Wording only: the decision not to ship frontend code from the integration stands | A |
| 6 | §6.4 interaction model: utterance `"{query}"` | "Each sample utterance must include a carrier phrase" (ASK slot-type-reference, AMAZON.SearchQuery) | Carrier-phrase utterances (e.g. "se {query}", "di {query}", "che {query}", "ho {query}"); Phase 0 checks the slot text arrives whole | A |
| 7 | §6.4 example `intent_script` handles only StopIntent | Unhandled intents answer "This intent is not yet configured within Home Assistant" (core `alexa/intent.py`, source) | Example also handles `AMAZON.CancelIntent` and `<skill-id>.SessionEndedRequest` | A |
| 8 | §6.4 one-word invocation name refused only at certification | it-IT rules: "One-word invocation names are not allowed" with no development/certification distinction (ASK choose-the-invocation-name) | Expect "dispensa di casa"; Phase 0 still tries "dispensa" in the console | A |
| 9 | §5.1 / §6.9 notification with an "Annulla" button via notify entities | `notify.send_message` has only `message` and `title` (home-assistant.io/actions/notify.send_message/) | Author decision: action buttons (Undo, add suggestion, confirm) only on Telegram (`inline_keyboard`) and in the dashboard; other notify entities get plain text | A |
| 10 | §10 / §11 minimum HA version | ToolResult, `integration` and annotations from 2026.10 (developer blog 2026-09-26); `pytest-homeassistant` first aligned release 2026.11.0 (PyPI); Marketplace 2026.11 | Minimum **2026.11.0**, Python ≥ 3.14.2 (author confirmed) | A |
| 11 | §11 README also covers installation from HACS on older versions | With a 2026.11 minimum no supported version lacks the Marketplace; separate HACS cannot be installed on 2026.11 (release 2026.11 notes) | No legacy-HACS section in the README (author confirmed) | A |
| 12 | §17.1 probatio: new code imports probatio | Allowed, not required ("Custom integrations need no changes"); LLM tool `parameters` are probatio schemas | Import `probatio` in new code, as the spec says | A |
| 13 | §17.1 quality scale "recommended for custom" | Page covers core only | Bronze remains a project goal, not a documented requirement | A |
| 14 | §17.1 Cloudflare `/compat` still works, REST API preferred | `/compat` "is deprecated" for single-model chat completions but "will continue to work"; new gateways require authentication | Only affects the author's provider choice and the community integration review | A |

### Multilingual requirement (author, 2026-11-07)

Everything user-facing is multilingual, Alexa included: translations for the integration, localized dashboard strings, LLM answers in the user's language, and Alexa interaction models plus `intent_script` examples for every supported language (at least en and it). How the Alexa request locale reaches `intent_script` must be checked in Phase 0.

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

## Phase 0 — skeleton decisions

| Topic | Decision | Source / reason |
| --- | --- | --- |
| `manifest.json` `version` | `0.0.0` is the unreleased placeholder; `tag-and-release.yaml` never publishes it and releases only when the version differs from the previous commit | Author rule: no release until asked |
| `iot_class` | `calculated`: the integration talks to no device or service itself; models, cameras and chats are reached through other HA entities | Manifest docs: `calculated` = "Does not talk to anything" |
| `categories` | `productivity_information.shopping_groceries` (primary), `ai_voice` | contributors.home-assistant.io/core/integrations/categories/; the HACS action fails on unknown values |
| `single_config_entry` | `true`: one household per HA instance (Bronze `unique-config-entry`) | Manifest docs |
| `__init__.py` | `async_setup_entry` / `async_unload_entry` return `None`; unload uses `async_unload_platforms_or_raise` | Developer blog 2026-10-23 "Config entry functions now return None" |
| Abort `single_instance_allowed` | No local translation: on the dev instance (2026.11.0) the `single_config_entry` abort carries `translation_domain: homeassistant`, so the central string is used | Developer blog 2026-09-28; verified on the dev instance |
| Tests | pytest-homeassistant 2026.11.0 configured in `pyproject.toml` as its PyPI page shows; `custom_components/__init__.py` makes the integration importable; custom integrations are enabled by the `hass` fixture | pypi.org/project/pytest-homeassistant |
| Beta CI | `pip install --pre --upgrade pytest-homeassistant` on top of the pinned stable; when no beta newer than stable exists, the job runs against stable | The package pins `homeassistant==<same version>` |
| Coverage gate | 90 % on the whole integration until `core/` and `storage/` exist, then restricted to them as the spec says | Spec §11 |
| Type checking | mypy strict on `custom_components/quartermaster` | Spec §9.3 |
| Dev dashboard loading | The dev instance mounts `../HomeAssistant-Quartermaster-Dashboard/dist` at `/local/quartermaster-dashboard/` and loads it with `frontend.extra_module_url`, avoiding the undocumented resource API | home-assistant.io/integrations/frontend/ |
| Brand images | Generated placeholders: `icon.png` 256², `icon@2x.png` 512², `logo.png` / `logo@2x.png` and `dark_` variants with height 256 / 512 | contributors.home-assistant.io/core/integrations/brands/ |
| Dev tooling deps | `scripts/dev-bootstrap` uses aiohttp through `uv run --with aiohttp`; it is a development tool, not an integration requirement | Same convention as OctopusEnergyIT |

## Phase 0 — practical checks

Procedure and probes: `docs/phase0/CHECKLIST.md`. Results are recorded here as they come in.

### Alexa on the Home Assistant side (simulated on the dev instance, 2026-11-07)

`POST /api/alexa` with hand-built requests against the `examples/phase0/packages/qm_probe_alexa.yaml` probe (HA 2026.11.0):

| Request | Result |
| --- | --- |
| LaunchRequest (intent_script keyed by skill ID) | "Dimmi", reprompt set, `shouldEndSession: false` |
| Intent with slot, stub returns an empty reprompt | `shouldEndSession: true`, no reprompt |
| Intent with slot, stub asks a question | Reprompt set, `shouldEndSession: false` |
| Intent without the slot | Handled with `query | default('')` |
| `AMAZON.CancelIntent` | Handled |
| `<skill-id>.SessionEndedRequest` | Handled, `reason` available |
| Stub delay 6 s / 9 s | Response after 6 s / 9 s; the Echo behaviour past 8 s is still to be checked |

### Alexa language (source code, not documentation)

`homeassistant/components/alexa/intent.py` (2026.11.0) passes only slot values to `intent.async_handle`: the request `locale` is dropped, so intent_script cannot know the user's language. Probe and proposed design:
- One intent per locale (`QuartermasterQueryIT`, `QuartermasterQueryEN`), each `intent_script` passing `language` to `quartermaster.ask`.
- The LaunchRequest is keyed by the skill ID only, so a multi-locale skill answers the launch in one fixed language.
- **Author decision (2026-11-08), following Amazon's "Develop skills in multiple languages":**
  - One skill with several locales and one backend.
  - One query intent per locale, passing `language` to `quartermaster.ask`.
  - The launch reply ("Dimmi" / "Tell me") follows the Home Assistant language. The next utterance arrives through the intent of the device language.
  - Not one skill per language.

### Cloudflare AI Gateway community integration (code review, v2026.04.0)

- **Blocker on HA 2026.11.** `entity.py` imports `voluptuous_openapi`, which HA no longer ships since 2026.9 (probatio). The conversation agent and AI Task entities fail to import. The fix is to use `probatio.to_openapi` in two places, as HA's `openai_conversation` does.
- **Other findings:**
  - It reads the deprecated `tool_result`, which logs a warning on every tool call; dict tool results break in 2027.11.
  - Streamed tool calls are kept only when `finish_reason == "tool_calls"`.
  - `structure` is sent as `json_schema` with `strict: true` while streaming.
  - Images are sent as base64 data URLs; multiple images are supported.
  - Chat calls `/compat` with only `Authorization: Bearer`, without `cf-aig-authorization`.
  - Security: tokens are redacted in diagnostics, there is no dynamic code, only Cloudflare hosts are contacted, and TLS verification is on. On a JSON parse failure the full model response is logged at ERROR level.
- **Consequence:** the author's planned provider route does not work on 2026.11 without a patch.
- **Author decision (2026-11-08):** wait for the upstream fix. Open PRs #4 and #5 add `voluptuous-openapi` to the requirements instead of switching to `probatio.to_openapi`. After the merge, re-check that tool and `structure` schemas (probatio objects) convert correctly, then run checklist section 6.

### Dashboard strategy in the "New dashboard" dialog (dev instance, 2026-11-08)

The bundle was loaded with `frontend.extra_module_url` (dev setup) and checked in headless Chrome:
- `window.customStrategies` holds the Quartermaster entry, and `ll-strategy-dashboard-quartermaster` is defined.
- The "Add dashboard" dialog lists **Quartermaster** under "Plance della comunità" (community dashboards) with the Italian description.
- A dashboard with `strategy: {type: custom:quartermaster}` renders the placeholder view in the user language: Italian, and English after switching the UI language. The sections view works at phone width (390 px) and desktop width.
- After choosing the strategy, the dialog asks for a title and URL that are not prefilled. The dashboard header shows the strategy's `title`.
- The "integration missing" message depends on `hass.config.components`, which lists loaded integrations, so it appears only after a restart once the integration is removed.

### Marketplace (not yet run)

Two documented requirements block the Marketplace part of checklist section 5. It waits for the author:
- Adding a custom repository needs a GitHub account linked with the device sign-in method.
- "By default, the Marketplace offers stable releases only". The repositories have no releases by design, and the docs do not say what happens with a repository without releases. Check this first once signed in. If a release is required, the author decides whether a test pre-release is allowed.

## Phase 1 — storage and inventory (started 2026-11-08)

Started at the author's request while the Phase 0 practical checks are still open. Phase 1 depends on none of them.

### Decisions

| Topic | Decision | Source or reason |
| --- | --- | --- |
| Where actions are registered | `async_setup`, with `CONFIG_SCHEMA = cv.config_entry_only_config_schema` | contributors.home-assistant.io, actions and quality-scale rule `action-setup` |
| Action target | `config_entry_id` (selector `config_entry`) is **optional**; when omitted, `service.async_get_config_entry` uses the only loaded entry and raises Home Assistant's own translated errors if there is none. **Deviation (A, author 2026-11-08)** from the `action-setup` rule ("make the target required"): Quartermaster allows a single entry (`single_config_entry`), and the ID changes on every reinstall, which would break the YAML for Alexa `intent_script`, automations and the docs examples. Passing the ID still works | contributors.home-assistant.io, actions and `action-setup`; author decision |
| Schemas | `probatio` with `cv` validators | Same pages; HA 2026.9+ |
| Responses | All Phase 1 actions use `SupportsResponse.OPTIONAL` and return `None` when no response is asked | Actions page |
| Admin-only actions | `export_data` and `import_data` use `async_register_admin_service`: they write or replace files in the config folder and import replaces the whole inventory | Actions page |
| Errors | `InventoryError(ServiceValidationError)` with keys under `exceptions` in both translation files. HA builds `str(err)` in English and strips the final period; the frontend shows the message in the user's language | `helpers/translation.py` 2026.11 (behaviour, not used as an API) |
| Database | `<config>/quartermaster/pantry.db`, WAL, foreign keys on, one connection used only from the executor and serialized by a lock, explicit `BEGIN IMMEDIATE` transactions | Spec §4 |
| Migrations | Numbered modules in `storage/migrations/`; the version is in `settings.schema_version`; each migration runs in its own transaction; a database from a newer version stops the setup (`ConfigEntryError`, `database_too_new`) | Spec §4 |
| Schema v1 | All tables of spec §4 are created now, including those used from Phase 2 onwards, to avoid early migrations | — |
| Schema v2 | `journal.operation_id` (not in spec §4), index on it; existing movements get one random id each. JSON export format version 2 (adds `operation_id`); version 1 files are refused, none were ever released | Author decision 2026-11-08 |
| Zone actions | `add_zone` (name, aliases, optional slug `zone_id`) and `remove_zone` (only when the zone holds no stock; its movements stay in the journal). Not in spec §6.7 | Author decision 2026-11-08 |
| Quantities | Decimal, rounded to 3 digits, never below zero (a consumption larger than the stock records the delta actually applied), at most 100,000 | Spec §4 and §8 (plausible values) |
| Undo | Works per **operation**: every `async_apply_changes` call is one operation and its journal entries share an `operation_id` (ULID). Undo reverts every movement of the newest operation that is not undone, applying the opposite of each journaled delta in reverse order, as one new operation of kind `undo` with `undoes_id` on each entry; undo entries are never undone themselves, and repeating undo walks back through older operations. The `undo_last` action takes the newest operation from any channel (like the spec's button); `only_channel` + user and the 10-minute window are ready for the LLM tool (Phase 3). A CSV import is still one operation per row | Spec §4, §6.1; author decision 2026-11-08 |
| Product names | Unique among active products, compared ignoring case and spaces; aliases may overlap and then resolve as ambiguous with the candidates | Spec §6.2 |
| Turning off `essential` | Clears `min_stock` and `target_stock` | — |
| Export/import location | `<config>/quartermaster/exports/`; `import_data` takes a plain file name in that folder, any path is refused | Spec §4; no path traversal |
| JSON export | Full state: zones, products, stock, journal, shopping items. Import validates the format, version, every row and every reference, then replaces everything in one transaction; pending changes and zone snapshots are cleared. Export then import gives the same state (tested) | Spec §4, Phase 1 criterion |
| CSV export | One row per product and zone (products without stock too). Import merges: creates missing zones and products and sets quantities as `import` movements; existing products are not modified | Spec §4 |
| Imports inside the package | Absolute (`custom_components.quartermaster...`), because the shared `.ruff.toml` bans relative imports from parent packages (TID252) | OctopusEnergyIT conventions |

### Verified on the dev instance (2026-11-08)

CSV import (2 zones, 2 products), consumption by alias, finish, undo, unknown-product error and CSV export all behave as in the tests. Exception text through the REST API is in English (see above).

### Open questions for the author

1. ~~Required `config_entry_id`~~: made optional (author, 2026-11-08), see the table.
2. ~~Zone actions~~: added (author, 2026-11-08).
3. ~~Operation id~~: added in schema v2 (author, 2026-11-08).

## Phase 2 — entities, shopping list and suggestions (2026-11-08)

### Decisions

| Topic | Decision | Source or reason |
| --- | --- | --- |
| Recalculation | A `DataUpdateCoordinator` recomputes to-buy, suggestions and expiring stock after every inventory write (debounced, 1 s cooldown) and every hour; the hour keeps time-based reasons current. **Deviation:** the spec asks for list and suggestions to update "in the same transaction" as the movement; here they follow within about a second, in a separate step, because the list is derived data and can always be rebuilt | Spec §4, §6.8; contributors.home-assistant.io (coordinator) |
| Automatic items | One per essential below minimum, quantity kept current, removed when no longer needed. Deleted by hand, it comes back while still below minimum | Spec §6.8; author decision 2026-11-08 |
| Manual items | Never removed automatically; text matched to a product by name or unique alias, so checking it can record the purchase | Spec §6.8 |
| Checking off | Records the purchase (quantity of the item, default 1, channel `dashboard`) in shopping mode; outside it only with the option, **off by default**. Without a zone to buy into, the item is checked and no purchase is recorded | Spec §6.8; author decision 2026-11-08 |
| `add_to_shopping_list` | New action (not in spec §6.7): product or free text, quantity, `from_suggestion`. A product already open on the list is not added twice; its quantity is updated | Author decision 2026-11-08 |
| Stale zones | Until zone checks exist (Phase 5), a zone's last update is its newest stock change | Spec §6.8 |
| Expiry | Expiring sensor uses the option threshold (default 3 days, expired stock included); the calendar shows one all-day event per product and zone with stock | Spec §6.6; contributors.home-assistant.io (calendar) |
| External copy | One-way, through the documented `todo.get_items`, `add_item`, `update_item`, `remove_item` actions; uid of a new external item found by summary among uids not seen before; items deleted on the external side are not re-added; failures only log a warning | Spec §6.8 |
| Import from todo | Action `import_from_todo` with targets products, essentials, shopping list; without `apply` it returns the preview only | Spec §4, §6.8 |
| Options | Options flow (`OptionsFlowWithReload`): external todo, record purchase on check, shopping mode minutes (120), stale zone days (7), expiring days (3) | contributors.home-assistant.io (options flow) |
| Update mode | `select` stored in the database `settings` table; used from Phase 4 | Spec §6.6 |
| Entities | One service device per entry; `has_entity_name` with translation keys | Quality scale (has-entity-name, entity-unique-id) |
| Deferred entities | Last check and zone error per zone, zone check buttons, show mode switches, photo requests, pending proposals, analyses of the day: they need Phases 4 and 5 | Spec §6.6 |
| Notification on "Go shopping" | Phase 8 (notifications); today the button and action refresh and turn shopping mode on, and the action returns the list and suggestions | Spec §6.5 |
| Schema v3 | `shopping_items.position`, `completed_at`, `note`; JSON export format 3 | — |

### Acceptance criteria

- An essential below minimum is listed once: tested (also after a manual delete and quantity changes).
- An essential between minimum and target is only suggested: tested.
- A stale zone gives the suggestion with the right reason: tested (with the stock-based zone update, see above).
- An accepted suggestion enters the list: tested (`add_to_shopping_list` with `from_suggestion`).
- Manual items stay intact: tested.
- Import from a test todo list with preview: tested with Home Assistant's own shopping list.
- Dev instance: entities created with Italian names; an essential below minimum appears on the list and in "Da comprare".

## Phase 3 — LLM tools and voice (2026-11-08)

### Decisions

| Topic | Decision | Source or reason |
| --- | --- | --- |
| Tools | `llm.py`, offered to the Assist API only while the entry is loaded: list_zones, get_stock, get_missing, get_suggestions, get_expiring, get_shopping_list, add_to_shopping_list, remove_from_shopping_list, adjust_stock, undo_last, set_essential, create_product. Named `quartermaster__*`, `integration` set, `ToolAnnotations` declared, `ToolResult` returned; inventory errors become `error` results | contributors.home-assistant.io LLM API; TECHNICAL_NOTES Phase 0 |
| Deferred tools | check_zone and arm_show_mode (Phase 5), confirm_pending (Phase 4) | Need zones and proposals |
| Tool argument defaults | Read with `.get()` defaults: in 2026.11 `async_call_tool` validates but passes the original arguments, so schema defaults are not applied | Observed in tests |
| Product search | Exact name or alias, then a rough singular/plural stem (formaggio/formaggi, cheese/cheeses), then category; limited to the zone and to products in stock when consuming; best stocked first; at most 3 candidates returned | Spec §6.2 |
| Ambiguity | Several candidates: no write, `ambiguous` with candidates and zones; a product held in several zones without a zone: `ambiguous_zone`. Within a quartermaster.ask session the held-back action, quantity and zone are kept, so the answer ("la mozzarella") completes the write even if the agent passes only the product | Spec §6.2, §6.4 |
| Not found | `not_found`, or `not_in_stock` for a known product at zero; the prompt tells the agent to offer the shopping list and product creation | Spec §6.2 |
| "Opened" | Sets `stock.opened_at`; not a movement, so not journaled or undoable | Spec §6.2 |
| Confirmations | set_essential and create_product always, purchases over 20 units: the tool returns `confirmation_required` with a single-use id (5 minutes, bound to the tool) and writes only when called again with it. The prompt asks the agent to get an explicit yes. Limit: the integration cannot verify that the user actually said yes; the id only forces a second, deliberate call | Spec §6.1, §8 |
| "Large" change | For a single voice or chat change, a purchase above 20 units. Consumption is bounded by the stock. Multi-product changes over 50 % (photos) are handled by proposals in Phase 4 | Spec §6.1, §8 (interpretation) |
| Channel and user | quartermaster.ask runs `conversation.process` with a child context and maps its id to the session, so the tools journal the right channel (alexa, telegram…) and user; any other call is `assist` | Spec §6.4, §8 |
| Alexa read-only | Option `alexa_read_only`: write tools return `read_only` for the alexa channel | Spec §6.4 |
| quartermaster.ask | Inputs text, channel (alexa, assist, voice, telegram, dashboard, service), language, session_key (default: the calling user). Session lifetime option (default 120 s). Reply within 5 s or acknowledge ("ci sto lavorando…") and fire `quartermaster_ask_result` when done; notifications pick it up in Phase 8. Agent errors give a spoken apology. Reprompt = last question of the reply | Spec §6.4 |
| Agent | Option `agent_id` (conversation agent selector); empty uses Home Assistant's default agent, which has no LLM tools | Spec §7 |
| Spoken phrases from Python | Under `common` in the translation files, read with `homeassistant.helpers.translation.async_get_translations`: public but not described in the integration guides. **Deviation (A, author 2026-11-08)** | Author decision |
| Manifest | `dependencies: ["conversation", "llm"]` | The tools and ask need both |
| Tests | `requirements_test.txt` adds the three requirements of HA's conversation component (versions from its 2026.11.0 manifest); the fixtures set up the core `homeassistant` component, which conversation needs | Test-only; the integration has no requirements |

### Acceptance criteria

- Ten test phrases in Italian and English produce the expected movements: tested with a **simulated agent** (scripted phrase → tool call) through quartermaster.ask, on the alexa channel. A real provider is still to be tried: it waits for an agent with an LLM on the dev instance (Cloudflare route blocked upstream; another provider needs the author's key).
- "ho finito il formaggio in frigo" with two cheeses asks and writes nothing; "la mozzarella" in the same session completes it: tested.
- "annulla" restores the quantity: tested.
- Unknown product leads to the shopping list question: the tool returns `not_found` and the prompt instructs the agent; the wording depends on the real model.
- Operations needing confirmation do not write without it: tested.
- Dev instance: `quartermaster.ask` answers in Italian; with the default agent (no LLM) it returns the spoken apology.

## Phase 4 — vision and photos (2026-11-08)

### Decisions

| Topic | Decision | Source or reason |
| --- | --- | --- |
| Image checks (Pillow, executor) | EXIF rotation applied, crop to the region of interest, refuse if the short side is under 640 px, mean luminance under 40/255 or Laplacian variance (at 640 px) under 25; resize to 1280 px long side, JPEG quality 80, re-encoded without any metadata | Spec §5, §7, §8. The darkness and blur thresholds are first guesses, to tune on real photos |
| Unchanged images | 64-bit dHash; at most 4 differing bits reuses the zone's last result (`zone_snapshots`) without calling the model | Spec §5 |
| AI Task call | Documented `ai_task.generate_data` action with response; the crop is written to `<media>/quartermaster/tmp/` only for the call, attached as `media-source://media_source/local/...` and deleted in `finally`; instructions list the zone's expected products with ids, aliases, unit and visual hints, ask for names in the HA language, and treat image text as data | Spec §5, §8, §17.2 |
| AI Task permissions | Calls from the integration carry no user, and AI Task checks permissions only for a user context (2026.11 source). Phase 0 question closed | `ai_task/permissions.py` |
| Structure | `nested` (object selector, `multiple`, `fields`) or the `text` fallback (JSON in a text field), chosen in the options; the result is validated: invalid items dropped, scores clamped to 0–1, quantities bounded, `matched_product_id` kept only if it was offered | Spec §4.2 |
| Matching | Offered id, else a unique name/alias/stem/category match; otherwise the item is "new" and needs a correction naming a product | Spec §5 |
| Proposals | `pending_changes` rows with the items (current and proposed quantity, confidence, name the model used), not-seen and unknown lists; expire after 24 h. Unchanged quantities are left out | Spec §4, §5 |
| Automatic mode | Applies items at or above the zone threshold (option, default 0.75) as one operation; the rest is proposed. When more than one product would change by over 50 %, nothing is applied automatically | Spec S12, §8 |
| Confirmation | `confirm_pending` action and LLM tool: all or chosen items, corrections (product, quantity), one operation; a correction to another product teaches the model's name as an alias | Spec §5 |
| Camera frames | `analyze_image` with a camera uses the documented `camera.snapshot` action into the temporary media folder, then reads and deletes the file; importing the camera component would pull in stream and numpy | home-assistant.io camera.snapshot |
| Files | `analyze_image` reads only paths allowed by `allowlist_external_dirs` | Spec §8 |
| Limits | Daily analysis limit (option, default 50), counted in the database, shown by a diagnostic sensor | Spec §7 |
| Insufficient result | For zone checks the "next source" rule belongs to the zone manager (Phase 5); a user photo always produces a proposal | Spec §5 |

### Acceptance criteria

- No write without confirmation in confirm mode: tested.
- At least 85 % of products recognized on 30 real labeled photos with a cloud provider: **not run**. It needs an AI Task entity with a vision model on the dev instance and the author's photos. `scripts/phase0/ai_task_probe.py` measures recall per photo.

## Planned extensions (author requests)

### Cameras as observation points (requested 2026-11-08, Phase 5; author answers the same day)

Cameras, when activated, track product movements as much as possible and raise each other's confidence:

- **Off by default.** A camera takes part only when configured on a zone, with the notice that frames show people. Only the crop goes to the provider, and frames are never stored.
- **Zone cameras** (`camera_show`): taking a product from, or returning it to, the zone they watch.
- **Passage cameras**, a new source type for places every trip may cross, such as a yard or an entrance:
  - they observe products carried in (possible purchase) or out (possible consumption);
  - on their own they **only propose**, never write.
- **Corroboration is positive only.** Observations of the same product by different cameras within a time window combine their confidence (1 − Π(1 − cᵢ)). A missing sighting never lowers confidence, because a product can leave without passing a camera (taken from the garage straight into a car, or carried out another way).
- **No double counting.** A passage observation that matches a recent zone observation (same product, within the window) only confirms it. It can turn a held proposal into a recorded consumption when the combined confidence reaches the threshold and the zone allows automatic writes. Otherwise it becomes its own proposal.
- **Window:** configurable, default 5 minutes. It only decides what can be matched; it is not a requirement to see the product twice.
- **Future cameras** (for example a living room) are zone cameras for their zone. The passage camera then confirms only what actually goes out.
