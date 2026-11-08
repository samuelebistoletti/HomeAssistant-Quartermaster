# Dashboard API (integration side)

Contract between the integration and the cards of `HomeAssistant-Quartermaster-Dashboard`. Version 1 (`api_version` in the snapshot).

## Websocket commands

| Command | Who | Fields | Result |
| --- | --- | --- | --- |
| `quartermaster/snapshot` | any authenticated user | — | snapshot (below) plus `can_write` |
| `quartermaster/subscribe` | any authenticated user | — | `success`, then an event with the snapshot now and after every recalculation |
| `quartermaster/journal` | any authenticated user | `limit` (1–500, default 50), `product_id` | `{entries: [{id, operation_id, ts, product_id, product, zone_id, zone, kind, delta, quantity_after, channel, undone}]}`, newest first |
| `quartermaster/upload_photo` | writers | `zone` (name, alias or id; required unless `kind` is `receipt`), `image` (base64 JPEG ≤ 2 MB, resized in the browser to 1280 px, quality 80, EXIF rotation applied, metadata removed), `kind` (`zone`, default, or `receipt`) | zone: analysis result, as the `analyze_image` action; receipt: as the `analyze_receipt` action (`{proposal_id, items, unknown}`) |
| `quartermaster/image` | any authenticated user | `proposal_id` or `zone_id` (one of them) | `{content_type: "image/jpeg", data: base64}` of the crop sent to the model for that proposal, or of the last zone analysis; error `not_found` when there is none |

Errors use the standard websocket error with `code` and, for inventory errors, `translation_domain: quartermaster`, `translation_key` and `translation_placeholders` (keys under `exceptions` in the integration translations). `not_loaded` when the integration is not set up; `unauthorized` for writes by non-writers.

Writers: administrators and the users chosen in the `write_users` option (`can_write` tells the card).

## Snapshot

```json
{
  "api_version": 1,
  "entry_id": "...",
  "entities": {"shopping_list": "todo.…", "to_buy": "sensor.…", "suggestions": "sensor.…", "expiring": "sensor.…", "pending_proposals": "sensor.…", "analyses_today": "sensor.…", "start_shopping": "button.…", "undo_last": "button.…", "update_mode": "select.…", "expiry": "calendar.…", "photo_requests": "sensor.…"},
  "zones": [{"zone_id", "name", "aliases", "sources": ["camera", "manual"], "source_details": [{"type": "camera", "entity_id": "camera.…", "roi": [0, 0, 1, 1], "...": "every field of the source, as accepted by set_zone"}], "check_schedule": "cron or null", "reminder_days": "int or null", "confidence_threshold": "float or null", "has_image": false, "updated": "ISO or null", "last_check": {"ts", "status": "updated|photo_requested|failed", "reason", "overall_confidence", "...": "..."} , "in_error": false, "show_armed_until": "ISO or null", "last_show": {"product", "action", "quantity", "confidence", "status", "ts"}}],
  "photo_requests": [{"request_id", "zone_id", "reason", "created"}],
  "products": [{"product_id", "name", "aliases", "category", "unit", "default_zone_id", "essential", "min_stock", "target_stock", "total", "stock": [{"zone_id", "quantity", "best_before", "updated"}]}],
  "to_buy": [{"product_id", "have", "quantity"}],
  "suggestions": [{"product_id", "have", "reason": "below_target|stale_zone|finished_recently|running_out", "...details": "have, target | zone, days | date | days_left, per_day"}],
  "expiring": [{"product_id", "zone_id", "quantity", "best_before", "days_left"}],
  "shopping_list": [{"item_id", "text", "product_id", "quantity", "origin": "auto|suggestion|manual", "done"}],
  "proposals": [{"proposal_id", "kind": "count|purchase", "zone_id": "null for purchase", "channel", "created", "has_image": false, "items": [{"key", "product_id", "product", "seen_as", "current", "proposed", "unit", "confidence", "notes"}], "not_seen"}],
  "shopping_mode_until": "ISO or null",
  "analyses_today": 0,
  "daily_analysis_limit": 50,
  "can_write": true
}
```

Entity ids depend on the user's language at creation time: always take them from `entities`.

## Writes (actions)

Per-zone entities (last check sensor, zone error, check button, show mode switch, last shown sensor) are not listed in `entities`; zones are checked with the `check_zone` action and show mode with `arm_show_mode`. `confirm_pending` with `items` confirms those items and leaves the others pending in the same proposal. An accepted below-target suggestion without quantity buys up to the target.

Cards write through the `quartermaster` actions (no `config_entry_id` needed): `adjust_stock` (product, operation consume|purchase|finish, quantity, zone), `set_stock` (product, quantity, zone, best_before), `add_product`, `set_essential` (product, essential, min_stock, target_stock), `add_to_shopping_list` (product or text, quantity, from_suggestion), `confirm_pending` (proposal_id, decision confirm|reject, items, corrections [{key, product, quantity}]), `undo_last` (operation_id), `start_shopping`, `add_zone`, `remove_zone`, `analyze_image`. The shopping list itself is the todo entity (`todo.*` actions). Actions refuse writes from users who are not writers (`Unauthorized`).

## Additions after version 1 (still `api_version` 1, additive only)

- **Proposal kinds.** `count` proposals (photos and cameras) set the quantity of a product in `zone_id`: `current` → `proposed`. `purchase` proposals (receipts and order emails) have `zone_id: null`; each item's `proposed` is the quantity bought and `current` is `null`. Confirming a purchase adds it to the product's default zone; corrections work the same way (`product`, `quantity`).
- **Images.** `has_image` on proposals and zones tells whether `quartermaster/image` has a crop. Only zone photos and zone camera checks keep a crop; camera_show and passage frames are never kept. A proposal's crop is deleted when it is confirmed, rejected or expires.
- **Zone editing.** `source_details`, `check_schedule`, `reminder_days` and `confidence_threshold` are what `set_zone` takes, so a card can edit a zone and send it back whole. Source types and fields:
  - `camera`: `entity_id`, `roi`, `max_age_minutes` (30), `max_checks_per_day` (4), and for interior cameras with a door sensor `trigger_entity` (binary_sensor), `trigger_state` (`off`: door closed), `trigger_delay_seconds` (3), `min_interval_minutes` (30).
  - `camera_show` and `passage`: `entity_id`, `trigger_entity`, `roi`, `capture`, `window_seconds`, `burst_interval_seconds`, `max_frames_to_model`, `cooldown_seconds`, `max_per_day`, `min_confidence`, `hours`, `announce` (notify entity ids for this zone's announcements); `camera_show` also `active`, `confirm`, `arm_minutes`; `passage` also `match_window_seconds`.
  - `manual`: no fields.
  Before a card enables `camera_show` or `passage` it must show the privacy notice (frames show people; only the show area goes to the AI provider; frames are deleted right after the analysis).
- **Running out.** Suggestion reason `running_out` with `days_left` and `per_day`: at the usual pace, the product runs out within the forecast days.
- **Analyses.** `daily_analysis_limit` next to `analyses_today`, for the AI usage tile (the integration has no cost data).
