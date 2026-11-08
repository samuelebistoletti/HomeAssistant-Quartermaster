# Dashboard API (integration side)

Contract between the integration and the cards of `HomeAssistant-Quartermaster-Dashboard`. Version 1 (`api_version` in the snapshot).

## Websocket commands

| Command | Who | Fields | Result |
| --- | --- | --- | --- |
| `quartermaster/snapshot` | any authenticated user | — | snapshot (below) plus `can_write` |
| `quartermaster/subscribe` | any authenticated user | — | `success`, then an event with the snapshot now and after every recalculation |
| `quartermaster/journal` | any authenticated user | `limit` (1–500, default 50), `product_id` | `{entries: [{id, operation_id, ts, product_id, product, zone_id, zone, kind, delta, quantity_after, channel, undone}]}`, newest first |
| `quartermaster/upload_photo` | writers | `zone` (name, alias or id), `image` (base64 JPEG ≤ 2 MB, resized in the browser to 1280 px, quality 80, EXIF rotation applied, metadata removed) | analysis result, as the `analyze_image` action |

Errors use the standard websocket error with `code` and, for inventory errors, `translation_domain: quartermaster`, `translation_key` and `translation_placeholders` (keys under `exceptions` in the integration translations). `not_loaded` when the integration is not set up; `unauthorized` for writes by non-writers.

Writers: administrators, or every user when the `non_admin_writes` option is on (`can_write` tells the card).

## Snapshot

```json
{
  "api_version": 1,
  "entry_id": "...",
  "entities": {"shopping_list": "todo.…", "to_buy": "sensor.…", "suggestions": "sensor.…", "expiring": "sensor.…", "pending_proposals": "sensor.…", "analyses_today": "sensor.…", "start_shopping": "button.…", "undo_last": "button.…", "update_mode": "select.…", "expiry": "calendar.…"},
  "zones": [{"zone_id", "name", "aliases", "sources": ["camera", "manual"], "updated": "ISO or null"}],
  "products": [{"product_id", "name", "aliases", "category", "unit", "default_zone_id", "essential", "min_stock", "target_stock", "total", "stock": [{"zone_id", "quantity", "best_before", "updated"}]}],
  "to_buy": [{"product_id", "have", "quantity"}],
  "suggestions": [{"product_id", "have", "reason": "below_target|stale_zone|finished_recently", "...details": "have, target | zone, days | date"}],
  "expiring": [{"product_id", "zone_id", "quantity", "best_before", "days_left"}],
  "shopping_list": [{"item_id", "text", "product_id", "quantity", "origin": "auto|suggestion|manual", "done"}],
  "proposals": [{"proposal_id", "zone_id", "channel", "created", "items": [{"key", "product_id", "product", "seen_as", "current", "proposed", "unit", "confidence", "notes"}], "not_seen"}],
  "shopping_mode_until": "ISO or null",
  "analyses_today": 0,
  "can_write": true
}
```

Entity ids depend on the user's language at creation time: always take them from `entities`.

## Writes (actions)

Cards write through the `quartermaster` actions (no `config_entry_id` needed): `adjust_stock` (product, operation consume|purchase|finish, quantity, zone), `set_stock` (product, quantity, zone, best_before), `add_product`, `set_essential` (product, essential, min_stock, target_stock), `add_to_shopping_list` (product or text, quantity, from_suggestion), `confirm_pending` (proposal_id, decision confirm|reject, items, corrections [{key, product, quantity}]), `undo_last` (operation_id), `start_shopping`, `add_zone`, `remove_zone`, `analyze_image`. The shopping list itself is the todo entity (`todo.*` actions). Actions refuse writes from users who are not writers (`Unauthorized`).
