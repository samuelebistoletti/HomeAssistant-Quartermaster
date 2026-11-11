# Assessment of the integration and the dashboard (2026-11-11)

Full review of `HomeAssistant-Quartermaster` and `HomeAssistant-Quartermaster-Dashboard`: code, tests, workflows, specification, technical notes and working memory. This file records what was found, what was fixed the same day, what is still open and the plan for it. Decisions taken while fixing are in `TECHNICAL_NOTES.md` ("Assessment and fixes").

## State

| | Integration | Dashboard |
| --- | --- | --- |
| Tests before | 208 passed, coverage 96.9 % | 106 passed |
| Tests after the fixes | 224 passed, coverage 96.8 % | 108 passed |
| Lint and types | ruff and mypy strict clean | ESLint and `tsc` clean |
| Build | n/a | `dist/` rebuilt from the sources |
| Dependencies | no extra requirements | `npm audit` clean; four dev dependencies one patch behind |

The architecture is sound: clear layers (`core`, `storage`, `vision`, `channels`), a storage interface, a journal with whole-operation undo, translations in parity, strict typing, a complete CI. The bugs below were in paths the tests did not exercise: restart and reload, non-admin users, default options, cameras with a crop.

## Fixed on 2026-11-11

Each one has a regression test unless noted.

| # | Problem | Where | Fix |
| --- | --- | --- | --- |
| 1 | A user outside `write_users` could write through the agent's tools and `quartermaster.ask` | `llm.py` | Write tools check `can_write` when the request has a user |
| 2 | The last Telegram message, and camera triggers, ran again when the entity came back with its restored state | `channels/telegram.py`, `vision/show.py`, `core/events.py` | Restored states (older than 30 s after appearing or leaving `unavailable`) are ignored |
| 3 | A checked automatic shopping item came back at once (default options) | `core/shopping.py` | Off the list until the stock changes or two days pass; completed items deleted after 30 days |
| 4 | A product finished and bought again kept the old best-before date | `storage/sqlite.py` | A batch that starts from zero takes its own date; undo keeps the old one |
| 5 | camera_show cropped its reference frame twice, so change scores were wrong with a crop | `vision/show.py` | The reference is the uncropped frame (covered by the existing show tests, no dedicated test) |
| 6 | Open dashboards stopped updating after any change of options | `channels/websocket.py`, `__init__.py`; dashboard `src/data.ts` | Subscription on a dispatcher signal that outlives reloads; the cards retry a refused subscription |
| 7 | A camera zone with an unchanged picture became "not updated" and collected duplicate proposals | `vision/analyze.py`, `core/zones.py`, `core/confirmations.py` | Snapshot time refreshed; the waiting proposal is reused; a new count replaces older ones |
| 8 | An essential at zero made every camera check end in a photo request | `core/zones.py` | Only essentials with stock in the zone count as missing |
| 9 | Photo reminders for manual zones repeated every day | `core/zones.py` | The interval counts from the last reminder too |
| 10 | The camera Undo button undid the camera's latest movement | `notify.py`, `channels/telegram.py` | The button carries the operation id |
| 11 | Quiet hours held answers to direct requests; the option was not validated | `notify.py`, `config_flow.py` | Direct messages bypass quiet hours; form validation with a translated error |
| 12 | Smaller ones | several | Local dates for expiry and "finished on"; Telegram errors in the chat's language; malformed buttons ignored; numeric product names; JSON import validation; decompression bombs; `"false"` as text; `items` attributes out of the recorder; temporary images cleared at startup; two triggers at once; missing todo item; products listed twice summed; lock on proposal confirmation; dashboard delay limit |

## Still open

Found by reading the code, not reproduced with a test.

| Topic | Problem | Proposed fix |
| --- | --- | --- |
| Entities | The todo entity, the buttons (Undo included), the update mode select and the show mode switches do not check `write_users` | Check the calling user from the entity context, or keep the documented limit (README says so today). Author's choice |
| "Confirm all" | Items without a product are dropped in silence and the proposal closes | Part of gap A.3 |
| External todo copy | The copy is matched by its text: a provider that rewrites the text gets a new copy at every sync; quantities and renames are not mirrored | Match by the uid returned right after `add_item` only once, store a marker when no uid is found, mirror renames |
| Order emails | The uid is marked as seen before the analysis (lost if the model fails); confirmation, shipping and delivery emails give three proposals | Mark after success; merge proposals of the same order number or the same items within a few days |
| Watchers | camera_show state and daily counters are keyed by source index: reordering sources shifts them; removed sources are never dropped; the repair issue survives a reload and shows the zone id | Key by a stable source id, prune on `async_start`, use the zone name |
| Zero movements | Consuming from a named zone with no stock records a movement with delta zero | Refuse with `not_in_stock` |
| camera_show fallback | Any model error triggers the single-image retry, which costs a second call that is not counted | Retry only on the provider's "one attachment" error or count both calls |
| Zone settings | `last_check:`, `failures:`, `source_reads:` and `show_count:` keys stay after a zone is deleted | Delete them with the zone |
| Journal channel | Dashboard actions are journaled as `service` | Pass the channel from the card (additive field) |
| Tool latency | Every write tool waits for a full recalculation, external todo copy included | Request a debounced refresh, or skip the external copy on that path |
| `max_age_minutes` | Accepted in the source schema, never enforced (already in the working memory) | Enforce or remove |
| Alexa | All devices of one account share one session | Add the device id to the session key when Alexa sends it |
| CI | The coverage gate is on the whole integration (the spec asks for `core` and `storage`); tools in the security workflow are not pinned | Add a second gate for the two packages; pin versions |

## Project gaps and plan

### A. Vision cannot remove products, and cannot start from nothing

A product not seen in a picture is only listed: vision never proposes zero. With an empty inventory the first photo or receipt gives only unknown items, which "Confirm all" drops.

1. Add "not seen → 0" rows to count proposals, not selected by default, never applied automatically.
2. Add the correction "create as a new product" to `confirm_pending`, the Telegram proposal message and the Proposals card.
3. Keep unknown items waiting instead of closing the proposal.

### B. No product life cycle

Nothing renames, edits, archives or merges a product (`archived` exists and is never set), nothing moves stock between zones, best-before dates can only be entered through an action, and a product with no stock row is invisible in the Inventory view.

1. Actions `update_product`, `archive_product`, `move_stock`; an LLM tool with confirmation.
2. Product editor in the Inventory card (name, aliases, category, unit, shelf life, best-before per zone).
3. Show products without stock in their default zone.

### C. Permissions are not uniform

Tools and `ask` are now covered. Entities are not (see "Still open"). One shared check, a test for every write path with a read-only user.

### D. Towards 1.0

- The domain `quartermaster` is used by another custom integration: the catalog may refuse it, and changing it after a release breaks entity ids, actions and the Alexa YAML. Decide before the first release.
- No contract test between `snapshot()` and the dashboard's `types.ts`: export a JSON schema or a sample snapshot from the integration tests and check it in the dashboard tests.
- No retention rule for the journal; no repair issue when the configured AI Task or agent disappears.
- Already tracked in `MEMORY.md`: recall on real photos (target 85 %), real cameras, provider table, Marketplace check.

### E. Cameras always ask; goods enter only with a receipt (author decisions, 2026-11-11)

Today a camera_show sighting above the confidence threshold is written at once (`confirm: auto`, the default) with an Undo button, a "returned" sighting with nothing to undo adds stock as a purchase, and in automatic update mode a zone camera's count is applied without asking. The author decided otherwise.

#### Rules

1. **Goods enter with a receipt or with a photo sent on purpose.** A confirmed receipt (photo or order email) is the normal way in, in each product's default zone as today. A photo a person sends of a shelf also loads what it shows and the stock does not have (a gift, a purchase without receipt). A camera never loads anything. There is no "placement allowance".
2. **A camera never writes.** Every sighting (camera_show, passage, scheduled or door-triggered count) becomes a request the user answers on Telegram. `confirm: auto` is removed from the sources.
3. **A photo sent on purpose shows the shelf as it is.** Quantities become the ones seen in the photo, lower or higher, following the update mode as today; products the inventory does not know are offered as new products (gap A).
4. **Telegram is where the user answers.** The same requests appear in the dashboard's Proposals view so none is lost; other notify entities get plain text, as decided earlier.
5. **No answer, no change.** A request expires (12 hours, an option) and nothing is written.

#### Taking products

1. The zone camera sees "Milk, taken, 1".
2. Sightings of the next two minutes (or until show mode turns off) are grouped in one request.
3. Telegram message: "Garage shelf: did you take 1 Milk? You had 3, 2 would be left." Buttons: **Yes, 1**, **2**, **All (3)**, **No**. With several products, one line and one row of buttons each, plus **Confirm all**.
4. On an answer the stock is lowered by that amount as a consumption (it feeds the forecast and the shopping list) and the message is replaced by the outcome with an **Undo** button.
5. A passage camera seeing the same product go out raises the confidence shown; it no longer confirms anything by itself.

Requests store a change (minus 1), not a target quantity, so an answer given hours later does not overwrite what happened meanwhile. Today a confirmed camera proposal is written as a count and does not feed the forecast.

#### Products seen being placed

A camera that sees goods placed on a shelf never changes the stock.

- **A receipt with that product was confirmed in the last 24 hours:** the sighting only confirms it. One summary: "Shopping put away: Milk 3 seen of 3 bought." A difference is reported, not applied: "I saw 4 Milk, the receipt says 3."
- **The same product was taken shortly before:** "Did you put 1 Milk back?" **Yes** / **No**. Yes undoes the earlier consumption; this is not a purchase.
- **Neither:** "I saw 2 Milk placed in Garage and I have no receipt for them. Send me the receipt." No button adds stock.

#### Counts

A count from a camera (scheduled or after a door closes) is always a proposal. Rows that lower a quantity, "not seen → 0" included (gap A), can be confirmed; rows above the recorded stock ask for the receipt or a photo sent on purpose instead.

#### Work

| Step | Content | Size |
| --- | --- | --- |
| E1 | Cameras never write: remove `confirm: auto` and the automatic path for the camera channel; passage corroboration stops confirming; "returned" never records a purchase; a camera count above the stock is never applied. Spec §5.1 and user guide updated | small |
| E2 | Movement requests: rows with a change and a kind (consume, put back), grouped in a time window, Telegram message with quantity buttons, confirmed as consumption in the journal, outcome message with Undo, expiry option | medium |
| E3 | Placement sightings matched with the receipts of the last 24 hours (read from the journal, no new table), summary and "send the receipt" messages | small |
| E4 | Dashboard: Proposals card shows movement requests (additive snapshot fields, API version unchanged) | small |
| E5 | Validation on the dev instance with the test bot, then on real cameras when the author says so | — |

#### Open question

Purchases said by voice or typed ("I bought three milk"), the plus button in the dashboard and checking an item in shopping mode keep working as they are; the author has not asked to change them.

### F. Zones nobody photographs: chest freezer, freezer, fridge (author request, 2026-11-11)

These zones have no camera, and a photo of a chest freezer shows only the top layer. The author keeps them by telling Alexa or the Telegram bot what was finished. Today such a zone works with a `manual` source and no `reminder_days`, with three weak points: essentials kept there raise "zone not updated" suggestions after seven days although nothing is wrong; nothing ever checks the list against reality; nothing says how long something has been frozen.

Proposal: a zone setting `tracking` with two values, `photo` (today's behaviour) and `ledger`.

A `ledger` zone:

1. **Never asks for photos** and is left out of the "zone not updated" suggestion and of photo reminders. A photo sent on purpose is still accepted.
2. **Goes in:** a confirmed receipt puts frozen products there through their default zone; home-made portions and gifts are said or typed ("I put two bags of ragù in the chest freezer"). Unknown names are offered as new products with that zone as default.
3. **Goes out:** by voice or Telegram, as today ("I finished the peas").
4. **Guided check on Telegram** instead of photos: on demand (`/check chest freezer`, or "let's check the chest freezer") and every `reminder_days` if set, the bot sends the zone's list, oldest first, with one row of buttons per product: **still there**, **−1**, **finished**; **all right** closes it. A typed answer ("no more peas, 2 ragù left") goes to the agent as usual. Answers are journaled as counts, and the zone counts as updated. On Alexa the same check reads at most five products per turn.
5. **Age:** a product placed in a ledger zone without a date gets today plus its `shelf_life_days` (for the freezer, the months it keeps), so the existing expiry sensor, calendar and daily message say "ragù in the chest freezer since June: use it". "What is in the chest freezer?" answers oldest first.

Work: a `tracking` field on the zone (migration, `set_zone`, zone editor in the dashboard), the three exclusions, the guided check (Telegram message with buttons, command, schedule through `reminder_days`), oldest-first ordering in `get_stock`. Medium size; independent of section E.

Until then: give these zones a `manual` source only and no `reminder_days`, and set `shelf_life_days` on what is frozen.

## Suggested order

1. E1 (cameras never write), because it changes behaviour the author does not want.
2. A (unknown and unseen products) and E2 together: both rework proposals.
3. E3 and F (ledger zones), then B (product life cycle) and E4 in the dashboard.
4. C and the "Still open" table.
5. D, field validation, then 1.0.0 when the author asks.
