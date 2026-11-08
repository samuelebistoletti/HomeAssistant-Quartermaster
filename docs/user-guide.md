# Quartermaster user guide

## First steps

1. Add the integration and open **Configure → AI models**. Choose:
   - a conversation agent with an LLM, with the **Assist** API attached;
   - an AI Task entity with a vision model.
2. Create your zones, for example with Developer tools → Actions:

   ```yaml
   action: quartermaster.add_zone
   data:
     name: Fridge
     aliases: [frigo]
   ```

   Or bring in an existing list. To import from a todo list (Bring!, a local list), run `quartermaster.import_from_todo` with `apply: false` first to see a preview. To import from a CSV file, put it in `<config>/quartermaster/exports/` and run `quartermaster.import_data` with its file name. The columns are `product`, `aliases` (separated by `;`), `category`, `unit`, `essential`, `min_stock`, `target_stock`, `barcode`, `shelf_life_days`, `zone`, `quantity` and `best_before`.
3. Mark your essentials, either by voice ("add coffee to the essentials, at least two packs") or with `quartermaster.set_essential`.
4. Create the Quartermaster dashboard (see the README).

## Zones and sources

Each zone has sources in priority order. A zone check (scheduled, by voice, from a button) tries them one after the other.

| Source | What it does |
| --- | --- |
| `camera` | Reads a frame, crops it, checks light and sharpness, skips unchanged images, asks the model. An unclear result, or an essential not seen, moves to the next source. |
| `manual` | Asks you for a photo, with one reminder after 12 hours. |
| `camera_show` | Show mode: when someone shows a product to the camera, it is recorded as taken (or put back). Off by default. |
| `passage` | A camera where people pass (entrance, yard). Products carried in or out are proposed, and confirm what a zone camera saw. It never writes on its own. |

Example:

```yaml
action: quartermaster.set_zone
data:
  zone: Garage shelf
  check_schedule: "0 19 * * *"        # every day at 19:00 (five-field cron)
  sources:
    - type: camera
      entity_id: camera.garage
      roi: [0.62, 0.05, 1.0, 1.0]     # crop x1, y1, x2, y2 as fractions
      max_checks_per_day: 2           # battery cameras
    - type: camera_show
      entity_id: camera.garage
      trigger_entity: event.garage_motion
      roi: [0.30, 0.10, 0.80, 0.90]
      min_confidence: 0.8
      hours: "07:00-23:00"
    - type: manual
```

Zones without a camera can have `reminder_days`: when they have not been updated for that many days, Quartermaster asks for a photo.

### Show mode

With `active: when_armed` (the default), show mode works only while the zone's **Show mode** switch is on. You turn it on from the dashboard, an automation, or by voice ("I'm taking things from the garage"), and it turns itself off after 10 minutes. Three triggers in a row without any change in the show area suspend it and create a repair issue. Turning it on again resumes it.

Frames show people. Only the show area goes to the AI provider, and frames are deleted right after the analysis.

### Cameras confirming each other

A sighting by a zone camera and one by a passage camera of the same product within the match window (default 5 minutes) combine their confidence. An uncertain take can then be recorded. A missing sighting never counts against a product: you may take something from the garage straight to the car.

## Photos

Send a photo through any of these:
- **Telegram**: with the zone name as caption, or pick the zone with the buttons;
- **Dashboard**: the photo card;
- **`quartermaster.analyze_image`**: with a file or a camera.

In **confirm** mode (the **Update mode** select), changes become a proposal. Confirm or reject it from the dashboard, Telegram or by voice, correcting product and quantity if needed. A correction teaches Quartermaster the name the model used. In **automatic** mode, changes above the confidence threshold are applied and the rest are proposed. When several products would change by more than half, nothing is applied automatically.

## Shopping

- An essential below its minimum goes on the list once, with the quantity up to its target.
- **Go shopping** (button, `quartermaster.start_shopping`, Telegram `/spesa`) sends the list and the suggestions and turns shopping mode on. While it is on, checking an item off records the purchase.
- Manual items are never removed automatically.
- To see the list in Bring!, Todoist or another app, set **Copy the shopping list to** in the options. Items you add there yourself are left alone.

## Undo

Every change is in the journal and can be undone:
- "annulla" or "undo" by voice or chat;
- the **Undo** button;
- the History view of the dashboard, operation by operation;
- `quartermaster.undo_last`.

## Backup

Your inventory is in `<config>/quartermaster/pantry.db` and is included in Home Assistant backups. To move it elsewhere, run `quartermaster.export_data`, which writes JSON (everything) or CSV (products and stock) to `<config>/quartermaster/exports/`. `quartermaster.import_data` with a JSON export restores exactly the same state.
