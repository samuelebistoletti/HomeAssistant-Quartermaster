# Quartermaster — Technical Notes

The current technical decisions, by topic, and every deviation from `docs/SPEC.md`. Each row states what holds today; superseded decisions live in git history. Open work and known bugs are GitHub issues; where the work stands is `docs/dev/MEMORY.md`.

When a decision changes, edit its row in place (with the date and who decided) instead of appending a new section. "Author" means the decision came from the project author.

## Deviations from the spec

| Spec | Deviation | Reason |
| --- | --- | --- |
| §6.1 tool names `QuartermasterListZones`, ... | Tools are named `quartermaster__list_zones`, `quartermaster__get_stock`, ... | Tools contributed through `llm.py` must be named `<domain>__<name>`; unprefixed names stop working in 2027.3 (contributors.home-assistant.io/core/internals/llm-api/) |
| §9.3 PRs to home-assistant/brands | No brands PR: the integration ships `brand/` (`icon.png` 256², `icon@2x.png` 512², `logo.png` / `logo@2x.png` and `dark_` variants, height 256 / 512; generated placeholders) | Since 2026.3 local brand images take precedence and pass the catalog check (contributors.home-assistant.io/core/integrations/brands/) |
| §9.1 / §11 tag equal to `version` | Tags `v<version>`; manifest `version` without `v` | Author (2026-11-07); a `v` prefix is not documented for the Marketplace |
| §6.4 utterance `"{query}"` | Carrier-phrase utterances, one intent per carrier word (see Alexa) | "Each sample utterance must include a carrier phrase" (ASK, AMAZON.SearchQuery); confirmed: a bare `{query}` sample fails the build |
| §6.4 one-word invocation name | "dispensa smart" (it-IT), "smart pantry" (en) | it-IT refuses one-word names and names with articles or prepositions; author (2026-11-10) |
| §5.1 / §6.9 "Annulla" button on any notify entity | Action buttons only on Telegram (`inline_keyboard`) and in the dashboard; other notify entities get plain text | `notify.send_message` has only `message` and `title`; author |
| §11 README covers HACS on older versions | No legacy-HACS section | With a 2026.11 minimum every supported version has the Marketplace; author |
| §6.7 actions | Added `add_zone`, `remove_zone` (only without stock; movements stay journaled) and `add_to_shopping_list` | Author (2026-11-08) |
| `action-setup` rule: target required | `config_entry_id` is optional; without it `service.async_get_config_entry` uses the only loaded entry and raises HA's translated errors if there is none | `single_config_entry`, and the id changes on every reinstall, breaking Alexa YAML, automations and doc examples; author (2026-11-08) |
| §4, §6.8 list and suggestions "in the same transaction" | Recomputed by a debounced coordinator within about a second | The list is derived data and can always be rebuilt |
| §4 journal | `journal.operation_id` (ULID) added: undo works per operation | Author (2026-11-08) |
| §5 debug retention of photos | No such option | Privacy; not needed so far; author |
| §5 step 2 `max_age_minutes` | Accepted in the source schema, not enforced: `camera.snapshot` gives no frame time; stale frames show up as unchanged images | No documented frame timestamp |
| §6.5 crop in proposals vs §5.1 privacy | Zone and proposal crops are kept until decided; camera_show and passage frames are never kept | §5.1 privacy wins |
| §17.2 Python-side phrases | Spoken and sent phrases under `common` in the translations, read with `homeassistant.helpers.translation.async_get_translations` (public, not in the integration guides) | Author (2026-11-08) |
| §8 user list | `hass.auth.async_get_users()` lists the users for `write_users` (public, not in the developer docs; there is no user selector); the check itself uses the documented `async_get_user` and `is_admin` | Author (2026-11-08): if the call changes, only the options step breaks |
| §5.1 camera writes | Cameras never write (see Zones and cameras) | Author (2026-11-11); spec updated |

## Platform facts

Checked against the official docs or, where marked, Home Assistant's source. Recheck when Home Assistant changes.

### Home Assistant

- Minimum 2026.11.0, Python ≥ 3.14.2: ToolResult, `integration` and tool annotations need 2026.10; the first aligned `pytest-homeassistant` and the Marketplace are 2026.11.
- An LLM API is available only once an administrator attaches it to a conversation agent or the MCP server; the README and config flow say so.
- A conversation can be continued only by the user who started it, and one message per conversation is processed at a time.
- `llm.ToolAnnotations(read_only, destructive, idempotent, open_world)`; defaults assume the worst. The MCP server exposes only read-only tools. `LLMContext` carries `platform`, `context`, `language`, `assistant`, `device_id`.
- 2026.11 `async_call_tool` neither validates arguments nor applies schema defaults: each tool validates with its own schema (`invalid_arguments`) and reads defaults with `.get()`.
- AI Task: checks permissions only for a user context, and calls from the integration carry none (source, `ai_task/permissions.py`). Multiple attachments and video are not documented. `structure` becomes a probatio schema.
- `async_setup_entry` / `async_unload_entry` return `None` (developer blog 2026-10-23). The `single_config_entry` abort uses the central `homeassistant` translation.
- `ServiceValidationError`: HA builds `str(err)` in English and strips the final period; the frontend shows the user's language.
- Event entities restore their last state after a restart.
- `camera.record` blocks for about `duration` seconds, raises `record_not_supported` without a stream and `already_recording` on a concurrent call; `lookback` needs an active HLS stream.
- Ring: `live_view` and `last_recording` (Ring Protect, disabled by default) cameras, doorbells and stickups only, cloud polled every 60 s. Alexa Devices: Speak and Announce notify entities, rate-limited.
- Custom dashboard strategies need 2026.5; element `ll-strategy-dashboard-<name>`; strategies regenerate on registry changes, not state changes. Rollup is the recommended Lit bundler. No documented API tells an integration whether a dashboard resource is installed: the README and config flow say the dashboard is a separate download.
- The "New dashboard" dialog lists the strategy under community dashboards with its translated description, and asks for a title and URL (not prefilled). The dashboard's "integration missing" message reads `hass.config.components`, so it appears only after a restart once the integration is removed.
- New code imports `probatio` (allowed, not required for custom integrations); LLM tool `parameters` are probatio schemas.
- `developers.home-assistant.io` redirects to `contributors.home-assistant.io`.

### Manifest and Marketplace

- `iot_class: calculated` ("Does not talk to anything": models, cameras and chats are reached through other entities); `integration_type: service`; `single_config_entry: true`; categories `productivity_information.shopping_groceries` (primary) and `ai_voice` (the HACS action fails on unknown values); `dependencies: conversation, llm, websocket_api`; `after_dependencies: ai_task`.
- The minimum Home Assistant version is set in `hacs.json` (`homeassistant`); the manifest has no such key.
- Catalog checks: archived, brands, description, hacsjson, images, information, integration_categories (1–3), issues, topics; `ignore` is not accepted. Install checks: loadable manifest, module present.
- Dashboard repositories are category `plugin`; the JS is looked up in the latest release assets, then the root, then `dist`, and registered as a `module` resource at `/local/community/<repo>/<file>?v=...`.
- `hacs.json` keys: name, content_in_root, zip_release, filename, hide_default_branch, country, homeassistant, hacs, persistent_directory (`render_readme` is undocumented).
- Adding a custom repository needs a GitHub account linked with device sign-in; "by default the Marketplace offers stable releases only", and nothing says what happens to a repository without releases.
- Quality scale: covers core only; Bronze is a project goal.

## Storage

| Topic | Decision |
| --- | --- |
| Database | `<config>/quartermaster/pantry.db`, WAL, foreign keys on, one connection used only from the executor, serialized by a lock, explicit `BEGIN IMMEDIATE` |
| Migrations | Numbered modules in `storage/migrations/`; version in `settings.schema_version`; one transaction each; a newer database stops setup (`ConfigEntryError`, `database_too_new`). All spec §4 tables were created in v1 |
| Quantities | Decimal, 3 digits, never below zero (a consumption larger than the stock records the delta applied), at most 100,000 |
| Export / import | Files in `<config>/quartermaster/exports/`, plain names only. JSON: full state, validated (format, version, rows, references) and replaced in one transaction; pending changes and snapshots cleared; round trip tested. Older format versions are refused (none released). CSV: one row per product and zone; import merges, creating zones and products and setting quantities as `import` movements, one operation per row |
| Admin-only actions | `export_data`, `import_data` (`async_register_admin_service`) |
| Imports | Absolute (`custom_components.quartermaster...`): the shared ruff config bans relative parent imports (TID252) |

## Inventory and undo

| Topic | Decision |
| --- | --- |
| Operations | Every `async_apply_changes` call is one operation; its journal entries share `operation_id` |
| Undo | Reverts every movement of the newest operation not yet undone, in reverse order, as a new `undo` operation with `undoes_id`; undo entries are never undone; repeating walks back. `undo_last` takes the newest operation from any channel; the LLM tool can restrict to its channel and user within 10 minutes |
| Product names | Unique among active products, ignoring case and spaces; aliases may overlap and then resolve as ambiguous |
| Essentials | Turning `essential` off clears `min_stock` and `target_stock` |
| New batch | Stock going from zero to positive takes that change's best-before date (or none) and clears `opened_at`; undo keeps the old values |
| Shelf life | A purchase without a date gets today + `shelf_life_days` |
| "Opened" | Sets `stock.opened_at`; not a movement, not undoable |
| Counting | `adjust_stock` action `counted` sets the quantity, journal kind `correction` |
| Errors | `InventoryError(ServiceValidationError)` with keys under `exceptions` |

## Shopping list, suggestions, forecast

| Topic | Decision |
| --- | --- |
| Recalculation | `DataUpdateCoordinator` after every write (debounced, 1 s cooldown) and every hour; sends the dispatcher signal `quartermaster_snapshot` after each run |
| Automatic items | One per essential below minimum, quantity kept current, removed when no longer needed; deleted by hand, it comes back while still below minimum. A completed item keeps its product off the list while no stock row changed after the check, at most two days (`CHECKED_GRACE`). Completed items older than 30 days are deleted locally |
| Manual items | Never removed automatically; matched to a product by name or unique alias |
| Checking off | Records a purchase (item quantity, default 1, channel `dashboard`) in shopping mode, outside it only with the option (off by default); without a zone to buy into, nothing is recorded |
| `add_to_shopping_list` | Product or free text, quantity, `from_suggestion`; an open item for the same product gets its quantity updated |
| Stale zones | A zone's last update is its newest stock change or successful check; ledger zones are excluded |
| Expiry | Sensor with the option threshold (default 3 days, expired included); calendar: one all-day event per product and zone with stock; local dates |
| Forecast | Rate = consumed units over the span since the first consumption in the last 60 days (at least 7 days, at least 3 consumptions, undone ones excluded); days_left = floor(total / rate). Within `forecast_days` (default 5, 0 = off): `running_out` suggestion, or an essential goes on the list (author) |
| External copy | One-way through `todo.get_items`, `add_item`, `update_item`, `remove_item`; a new item's uid is found by summary among unseen uids; items deleted externally are not re-added; failures log a warning |
| Import from todo | `import_from_todo` (products, essentials, shopping list); without `apply` it returns a preview |

## LLM tools and `quartermaster.ask`

| Topic | Decision |
| --- | --- |
| Tools | Offered to the Assist API while the entry is loaded: list_zones, get_stock, get_missing, get_suggestions, get_expiring, get_shopping_list, add_to_shopping_list, remove_from_shopping_list, adjust_stock, undo_last, set_essential, create_product, check_zone, arm_show_mode, confirm_pending. Inventory errors become `error` results |
| Product search | Exact name or alias, then a singular/plural stem, then category; limited to the zone and to stocked products when consuming; best stocked first; at most 3 candidates |
| Ambiguity | Several candidates: no write, `ambiguous` with candidates; a product in several zones without a zone: `ambiguous_zone`. Within an `ask` session the held-back action, quantity and zone are kept, so "la mozzarella" completes the write |
| Not found | `not_found`, or `not_in_stock` for a known product at zero; the prompt offers the list and product creation |
| Confirmations | set_essential, create_product and purchases over 20 units return `confirmation_required` with a single-use id (5 minutes, bound to the tool) and write only on the second call. The integration cannot verify the user said yes; the id forces a deliberate second call |
| Permissions | Write tools check `can_write` when the context has a user (`not_allowed`); `ask` passes the caller's user. Requests without a user are checked by their channel |
| Shopping list prompt | The prompt says to use only the `quartermaster__` list tools: with HA's own Shopping list loaded, a model wrote to that list instead |
| `ask` inputs | text, channel (alexa, assist, voice, telegram, dashboard, service), language, session_key (default: the calling user); session lifetime option (120 s); a child context maps to the session, so tools journal the right channel and user |
| `ask` timing | Spoken channels (Alexa, Assist) acknowledge after 5 s ("ci sto lavorando…") and fire `quartermaster_ask_result` when done; without a destination for late voice replies the acknowledgement promises no message. Written channels (Telegram, dashboard, service) wait up to 90 s and send one reply |
| `ask` sessions | One lock per session: a request waits for the previous one instead of failing with "This conversation is busy" |
| Tool calls written as text | Removed from the reply (Llama lists, `domain__tool(...)`, `<tool_call>`, `<|python_tag|>`, JSON name/parameters), with "Part of the request was not carried out: check and try again" |
| Agent | Option `agent_id`; empty = HA's default agent, which has no LLM tools (spoken apology). Agent errors give a spoken apology. Reprompt = the reply's last question |
| Limits | 20 `ask` requests per channel and minute |
| Alexa read-only | Option `alexa_read_only`: write tools return `read_only` on the alexa channel |

## Vision, photos and proposals

| Topic | Decision |
| --- | --- |
| Image checks (Pillow, executor) | EXIF rotation, crop to the region, refuse short side < 640 px, mean luminance < 40/255, Laplacian variance (at 640 px) < 25; resize to 1280 px long side, JPEG 80, no metadata. Thresholds are first guesses to tune on real photos |
| Receipts | `prepare_document`: no short-side or sharpness checks, long side 1600 px |
| Unchanged images | 64-bit dHash, ≤ 4 differing bits reuses the zone's last result: refreshes the snapshot time and returns the waiting proposal with `unchanged: true` (no notification) |
| AI Task call | `ai_task.generate_data` with response; the crop goes to `<media>/quartermaster/tmp/` only for the call (`media-source://media_source/local/...`), deleted in `finally` and at startup; instructions list expected products with ids, aliases, unit and hints, ask for names in the HA language and treat image text as data |
| Structure | `nested` or `text` fallback (JSON in a text field, `result_json` for camera sources), chosen in the options; results validated: invalid items dropped, scores clamped, quantities bounded, `matched_product_id` kept only if offered |
| Matching | Offered id, else a unique name/alias/stem/category match; otherwise "new", needing a correction |
| Proposals | `pending_changes`, expire after 24 h; unchanged quantities left out; same-product rows summed with the lowest confidence. A new count expires the zone's earlier waiting counts. Kinds: `count`, `purchase` (receipts: confirmed into each product's default zone as one operation), `movement` (cameras) |
| Products not seen | An `expected_not_seen` name matching, by name or alias only, exactly one stocked product in the zone becomes a zero row with `missing: true`: never automatic, not a large change, left waiting by a plain confirm, applied only by its own key (Telegram `/qm_gone <proposal> <key>`, a ticked row in the dashboard, `items` in the action or tool). Author: option B, 2026-11-11 |
| Unknown items | A row matching no product (`product_id` null) is never applied by a plain confirm or by its key alone: it stays in the proposal (returned as `waiting`) and expires with it. A correction with `new_product` creates it (name = `name` or `seen_as`, unit as read, default zone = the proposal's zone; a purchase needs `zone`) and applies its quantity in the same confirmation; every name and zone is checked before the first product is created. A given name keeps `seen_as` as an alias. Telegram: `/qm_new <proposal> <key>`, and for a purchase `/qm_newz <proposal> <key> <zone>` after a zone question. The confirmation itself is the yes (no separate `create_product` confirmation id). Camera proposals refuse `new_product` (`camera_no_new_product`): a camera never loads goods. Author, 2026-11-12: zone asked for receipts, name as read and editable. `unknown_items` (things with no name, e.g. "jar without label") stay informative |
| Automatic mode | Items at or above the zone threshold (default 0.75) apply as one operation, the rest is proposed; nothing applies automatically when more than one product would change by over 50 %. Photos only: cameras never write |
| Confirmation | `confirm_pending` action and tool: all or chosen items, corrections (product, quantity), one operation; a correction to another product teaches the model's name as an alias |
| Files | `analyze_image` reads only `allowlist_external_dirs` paths; camera frames through `camera.snapshot` into the temporary folder (importing the camera component pulls in stream and numpy) |
| Crops | `<config>/quartermaster/crops` (not browsable): the last zone crop and each proposal's crop, deleted on decision, expiry and hourly housekeeping; served by `quartermaster/image` |
| Limits | Daily analyses (default 50, counted in the database, diagnostic sensor), `hourly_analysis_limit` (default 30) on every model call; camera reads time out after 15 s |
| AI cost | AI Task reports no cost: the dashboard shows analyses today against the limit (author) |

## Zones, sources and cameras

| Topic | Decision |
| --- | --- |
| Zone settings | `set_zone` (name, aliases, sources, cron schedule, reminder days, threshold), sources validated per type; edited from the dashboard and by action, no options-flow editor |
| Schedules | In-house five-field cron, checked every minute; scheduled checks and housekeeping run as config-entry background tasks |
| Insufficient result | Unusable image, confidence under the threshold or an expected stocked essential not seen → next source |
| Zone error | On after 3 consecutive failed checks, cleared by a success |
| Photo requests | `photo_requests` table; one open request per zone; event `quartermaster_photo_request` with reason (`not_recognized` lists the products to frame); one reminder after 12 h, expiry after 24 h; the interval counts from the later of the last update and the last reminder; closed by any successful analysis. A check started from a Telegram chat sends the request back to that chat (chat id from the `ask` session) |
| Per-zone entities | Added and removed with zones and camera_show sources without reloading; a renamed zone keeps its old entity names until restart |
| Restored states | An event entity appearing or leaving `unavailable` with a state older than 30 s shows a restored state: the Telegram channel and the camera triggers ignore it; a binary sensor back already on is not a trigger |
| Cameras never write | camera_show and passage sightings become `movement` proposals (`ProposalService.async_propose_movement`): one row with `moved`, confirmed as a consumption (`moved` may be corrected; a corrected `quantity` is the count left) or as the undo of `operation_id` for a product put back. `quartermaster_show_request` asks on Telegram (`/qm_ok`, `/qm_qty <id> <quantity>`, `/qm_no`); `quartermaster_show_recognized` fires on confirmation with Undo. Sightings under `min_confidence` wait in the dashboard silently until a passage camera supports them. `confirm` was removed from the source schema. Each sighting is its own request; requests expire with the 24 h proposal lifetime (author, 2026-11-11) |
| Goods coming in | Only a confirmed receipt or a photo sent on purpose loads stock. "Returned" with nothing to undo and passage "in" record nothing; a camera count proposes decreases only and lists the rest in `more_than_stock` (author, 2026-11-11) |
| Placed goods vs purchases | A "returned" sighting is matched in this order: a take of the last 10 minutes (cancel the waiting request, or ask to undo the confirmed consumption); then the product's `purchase` journal entries of the last 24 h, less undone ones (any channel: the journal does not record that a purchase came from a receipt, and a purchase said by voice or checked off the list is goods that came in too); then the receipt request. A match writes nothing and fires `quartermaster_show_put_away` with `seen` (placed sightings of the product since the first of those purchases, kept in memory) and `bought`; below `min_confidence` it is matched silently. Notifications sum up the events of 2 minutes in one camera message and report differences only. No new table (issue #3) |
| camera_show capture | Burst of snapshots for `window_seconds`; frames cropped and scored (Laplacian sharpness, difference from the reference scene, dHash duplicates); up to `max_frames_to_model` sharp distinct frames; one single-frame retry if the provider refuses several. `clip` and `auto` use burst until the real-camera check |
| Reference scene | The uncropped first frame; replaced by the last frame after a trigger with no change |
| Suspension | 3 triggers in a row without change suspend the source with a repair issue; arming again clears it |
| Arming | `show_mode` switch per zone, `arm_show_mode` action and tool, auto-off after `arm_minutes` (10) |
| Passage cameras | Source type `passage` (trigger, area, match window, default 300 s) for places every trip crosses. They propose only. Corroboration is positive only: same product, different cameras, within the window → 1 − Π(1 − cᵢ); a missing sighting never lowers confidence; a passage sighting matching a zone sighting only confirms it (author, 2026-11-08) |
| Door cameras | Camera source `trigger_entity`, `trigger_state` (off), `trigger_delay_seconds` (3), `min_interval_minutes` (30); an unclear result is dropped without photo request or failure |
| Announcements | camera_show and passage take `announce` (notify entities); the message says when an essential went on the list |
| Daily camera limits | Every read attempt counts, failed ones too (battery cameras) |
| Privacy | Cameras are off by default; only the crop goes to the provider; frames are never stored; `set_zone` and the zone editor carry the notice that frames show people |
| List check (`ledger`) | Source type for any zone a photo shows badly (closed cupboards, fridge, freezers), kept by voice and Telegram. `check_days` (default 30, 0 = on request). Guided setup asks per zone "photo" or "list". After a `camera` source the list is only the fallback. `check_zone` sends at most 30 products, oldest first (best-before, then last change), via the photo-request notification type; Telegram `/qm_lg <zone> <product> m|f`, `/qm_lg_ok <zone>`, `/check`; last sent time in `ledger_check:<zone>`. Excluded from photo reminders and `stale_zone`. By voice `check_zone` takes `start` (`next_start`) and reads five products per turn (author, 2026-11-11) |

## Channels

### Telegram

Event attributes as observed on the dev bot (polling, 2026-11-10):

| Sent | Event (`event_type`, attributes) |
| --- | --- |
| Text | `telegram_text`: `text`, `chat_id`, `user_id`, `date` (a datetime: never pass to `tojson`), `message_thread_id`, `bot` |
| Command | `telegram_command`: `command`, `args` |
| Photo or image file | `telegram_attachment`: caption in `text` (or null), `file_id` (largest size), `file_mime_type`, `file_size`, `file_name` for files |
| Button | `telegram_callback`: `id`, `data`, `message`, `chat_id`, `user_id` |
| Bot message | `telegram_sent`: `chat_id`, `message_id` |

| Topic | Decision |
| --- | --- |
| Input | The bot's event entity (option), state changes only; the last handled state is remembered |
| Allowed chats | Quartermaster's own list on top of the bot's allowlist (core accepts a message if the user *or* the chat is allowed); groups only with the option |
| Rate limit | 20 messages per chat per minute |
| Texts | `ask` session per chat (`chat:<id>`, channel telegram), one reply |
| Photos | `telegram_bot.download_file` without a directory (`<config>/telegram_bot`, returns `file_path`; a custom directory must be allowlisted), read and deleted at once; caption = zone, otherwise zone buttons (photo held 10 minutes); the receipt phrase or button routes to `analyze_receipt` |
| Buttons | `inline_keyboard` rows `Label:/command`, labels cleaned of `,` and `:`; answered with `answer_callback_query`; malformed buttons ignored. Commands: `/qm_ok`, `/qm_no`, `/qm_qty`, `/qm_zone`, `/qm_undo`, `/qm_undo_camera <operation_id>`, `/qm_add`, `/qm_gone`, `/qm_lg`, `/qm_lg_ok`, `/qm_chk <zone>` |
| Commands | `/list` `/lista`, `/shopping` `/spesa`, `/undo` `/annulla`, `/check` (without a zone, the bot's menu: zones come back as buttons), anything else shows help. Help text has no angle brackets (hassfest) |
| Replies | `send_message` with `chat_id`, the bot's `config_entry_id` and `parse_mode: plain_text`, in the chat's language |
| Other facts | Only polling and webhooks receive messages; legacy `notify.telegram` was removed in 2026.11; the bus carries the same events |

### Websocket and dashboard API

Contract in `docs/dashboard-api.md`. Reads for every authenticated user; writes for administrators and `write_users`, checked in the commands and in write actions when the call carries a user (calls without a user are allowed). Entity ids are resolved by unique id. Uploads at most 2 MB after browser resizing. `quartermaster/subscribe` follows the `quartermaster_snapshot` dispatcher signal, so it outlives reloads; the dashboard retries a refused subscription. The dev instance loads the dashboard bundle with `frontend.extra_module_url` from `../HomeAssistant-Quartermaster-Dashboard/dist`.

### Order emails

IMAP's `imap_content` event (filtered on `entry_id`, `initial`, allowed senders) and `imap.fetch` for the full text. Sender rules: whole address, or domain with subdomains. Processed uids kept in settings. Text goes to the text AI Task (`ai_task_text`, falls back to the image one); the prompt marks the email as data. Always a proposal (author).

## Notifications

| Topic | Decision |
| --- | --- |
| Routing | Option per message type (photo requests, check results, expiring, camera, essentials running out, shopping, weekly, late voice replies) with notify entities in order, first available wins; `notify.send_message` |
| Telegram copy | Option `telegram_notify` copies every message to the allowed private chats with buttons |
| Quiet hours | "HH:MM-HH:MM", validated; held messages wait in memory and are lost on restart (author); `direct` messages (photo request to the asking chat, late voice reply) are not held |
| Timings | Expiring daily at 9:00; weekly summary Monday 9:00; essentials when they newly go below minimum; list and suggestions on Go shopping |
| Texts | `common` translations; dates as day/month or month/day per language |
| `notify.py` | Holds the router; Quartermaster provides no notify entities |

## Alexa

| Topic | Decision |
| --- | --- |
| Skill | One skill, several locales, one backend; launch reply ("Dimmi" / "Tell me") in the HA language (author, 2026-11-08, after Amazon's multi-language guide) |
| Locale | HA's `alexa/intent.py` passes only slot values (source): the request locale is lost, so each intent is per locale and passes `language` |
| Carrier words | Alexa sends only the intent name and slots, never the transcript, and the carrier word is not part of the slot ("ho finito il formaggio" → `finito il formaggio`). Dropping it changes the meaning, so each carrier word has its own intent (`QuartermasterIT_se`, `QuartermasterEN_how_much`, ...) whose `intent_script` passes `"<word> {{ query }}"`. `scripts/alexa-models` generates the models and YAML from one list per language; a test checks they are current (author, 2026-11-10) |
| Yes / no | Per-locale custom intents `QuartermasterYesIT/NoIT/YesEN/NoEN` (the built-in Yes intent cannot tell the language) |
| "And then?" | `Quartermaster<LANG>_reply_more` ("e poi", "continua", "and then") sends "e poi?" / "and then?" to `ask` |
| Session end | Also handles `AMAZON.CancelIntent` and `<skill-id>.SessionEndedRequest` (unhandled intents answer "not yet configured"); an empty reprompt ends the session; a slot without value leaves its variable undefined |
| Lambda | Forwarding Lambda in eu-west-1 (documented for UK/DE/ES/FR; Italian is not listed); timeout 10 s (the 3 s default fails). Echo Show devices may keep the screen session open about 30 s |
| Observed on a real Echo (Alexa+) | Replies after 6 s and 9 s are spoken; "di' a X che …" is an Announcement in Italian, so it is not documented; "chiedi a X di …" swallows the "di"; Alexa writes "una" as `1`; free-text recognition is imperfect. Alexa+ prefixes each session's first reply with a handoff phrase that nothing turns off |
| Future | The Alexa+ MCP Toolkit (MCP server without a skill) is limited to select partners; re-evaluate when it opens (author, 2026-11-10) |

## Configuration

| Topic | Decision |
| --- | --- |
| Actions | Registered in `async_setup` with `cv.config_entry_only_config_schema`; probatio schemas; `SupportsResponse.OPTIONAL` |
| Guided setup | `user` (name, language) → `models` → `zones` → `zone` per zone (camera, crop, photo or list) → `channels` → `notifications` → `essentials`. Settings are the options-flow keys, saved with `async_create_entry(options=...)`. Zones pre-filled with Pantry, Fridge, Freezer in the chosen language; crop typed as `x1, y1, x2, y2`. The flow cannot reach the database, so zones, essentials and the todo import wait in `entry.data` and the first setup applies and removes them, skipping failing items |
| Entry name | The setup suggests "Quartermaster"; entity names are "<entry name> <entity name>" and keep that prefix (author) |
| Options | Menu of groups (`async_show_menu`), one form each; a submitted group replaces only its keys; `OptionsFlowWithReload` |
| Language | Option `language` (en, it) for phrases sent without a language, then the system language; entity names follow the user's UI language |
| Permissions | Writes for administrators and `write_users`. Entities (todo, buttons, select, switch) rely on Home Assistant entity permissions (README states the limit) |
| Diagnostics | Options with entity ids, chats and agent redacted; only counts, source types and statuses |
| Dashboard | Accepted for now (author, 2026-11-08): tile links from the current URL, picker language, upload errors translated by the dashboard, Go shopping not permission-checked, a reload after adding or deleting a zone |

## AI providers

| Topic | Decision |
| --- | --- |
| Route | Cloudflare Workers AI through the community Cloudflare AI Gateway integration (`michaeldwan/ha-cloudflare-ai-gateway`). It imports `voluptuous_openapi`, gone since HA 2026.9; `scripts/dev-cloudflare-gateway` installs upstream `main` with `entity.py` switched to `probatio.to_openapi(..., openapi_version="3.1.0")`, on the dev instance only, until upstream fixes it (author, 2026-11-08) |
| Gateway facts | `/compat` is deprecated for single-model chat but "will continue to work". Chat goes to `/compat` with `Authorization: Bearer` (works on an authenticated gateway); the image AI Task sends `cf-aig-authorization`; the integration verifies a **user** API token. Workers AI accepts `json_schema` with `strict: true` and Quartermaster's optional fields. Other upstream findings: deprecated `tool_result` read (warning per call, breaks in 2027.11), streamed tool calls kept only with `finish_reason == "tool_calls"`, full model response logged at ERROR on a JSON parse failure |
| Models | gemma-4-26b for the conversation agent and the image AI Task; glm-4.7-flash for the text AI Task (author, 2026-11-11). glm as an agent undid a change nobody asked for and streams its reasoning; gemma was slower and less precise than llama-4-scout on receipts: watch the first real receipts. Excluded: mistral-small-3.1 (followed a prompt injection), gpt-oss-20b (ignores the schema), llama-3.3-70b (missed items, no writes as agent), gpt-oss-120b as agent (needs an integration patch), partner models (need gateway credits) |
| Token use | An agent call carries 4,500–20,000 input tokens: Assist prompt ~2,500 characters, Quartermaster tools ~6,200, HA's own tools ~4,500, plus exposed entities. Exposing fewer entities is the main lever; trimming tool schemas would save about a tenth |
| Billing | After exceeding the Free plan's 10,000 neurons/day, Workers AI refused every call (429, code 4006) for over 24 h despite the reset; Workers Paid lifted it. Gateway Unified billing charges prepaid credits and records 0 neurons; Standard billing keeps the free 10,000 a day |
| Benchmark | `scripts/dev-agent-bench` (`--quick`: 6 phrases); a write changing a product other than the one named fails (`side_effects`); the script stops on a quota error |

## Tooling and CI

| Topic | Decision |
| --- | --- |
| Tests | pytest-homeassistant pinned in `requirements_test.txt` (it pins `homeassistant` to the same version), configured in `pyproject.toml`; `custom_components/__init__.py` makes the package importable; the fixtures load the core `homeassistant` component and HA conversation's three requirements (from its manifest) |
| Beta CI | `pip install --pre --upgrade pytest-homeassistant` on top of the pin |
| Coverage | 90 % gate on the whole integration (the spec asks for `core` and `storage`) |
| Types | mypy strict on `custom_components/quartermaster` |
| Dev tools | `scripts/dev-bootstrap` uses aiohttp via `uv run --with aiohttp`; Phase 0 probes in `examples/phase0/` and `scripts/phase0/` never ship (procedure: `docs/phase0/CHECKLIST.md`) |
| Domain | `quartermaster` is also used by `quartermaster-app/ha-quartermaster` (releases since 2026-10): both cannot be installed together and the catalog may object. Kept by the author (2026-11-07); the README does not mention it |
