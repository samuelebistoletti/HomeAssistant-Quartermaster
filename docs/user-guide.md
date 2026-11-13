# Quartermaster user guide

## First steps

1. Add the integration. The setup asks for:
   - a conversation agent with an LLM, with the **Assist** API attached;
   - an AI Task entity with a vision model;
   - your first zones, each with an optional camera;
   - Telegram, notifications and your first essentials, all optional.

   Everything can be changed later under **Configure**.
2. Add more zones when you need them, for example with Developer tools → Actions:

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
| `camera_show` | Show mode: when someone shows a product to the camera, Quartermaster asks on Telegram whether it was taken, and how many, before recording anything. Off by default. |
| `passage` | A camera where people pass (entrance, yard). It supports what a zone camera saw and asks about goods carried out; goods carried in are left to their receipt. It never writes on its own. |
| `ledger` | List check, for any place a photo shows badly (a closed cupboard, a fridge, a freezer): instead of a photo request, the zone's list is sent to go through, every `check_days` (30 by default, 0 = only on request). After a camera it is the fallback when the frame is unclear. |

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

An interior camera (fridge, cupboard) can check the zone when its door closes: add `trigger_entity` (the door's binary sensor) to the camera source, and optionally `trigger_state` (`off`, closed, by default), `trigger_delay_seconds` (3) and `min_interval_minutes` (30). Such a check uses that camera only: when its frame is unclear nothing happens, and no photo is requested.

Zones without a camera can have `reminder_days`: when they have not been updated for that many days, Quartermaster asks for a photo.

### Cameras never write

A camera only tells you what it saw; nothing changes until you answer. Goods come in with a confirmed receipt or with a photo you send of the shelf, never from a camera:

- **Taken:** "Garage shelf: did you take 1 passata? You had 4, 3 would be left." Buttons: the quantity seen, one more, all, and No. On an answer the consumption is recorded and the outcome comes with **Undo**. Unanswered requests expire after 24 hours and change nothing.
- **Put back:** while the request for the take is still waiting, the two cancel out. After you confirmed the take, Quartermaster asks whether to undo it. When the product was bought in the last 24 hours (a confirmed receipt, an order email or any purchase you recorded), the sighting only confirms the shopping: one message a couple of minutes later sums up what was put away ("Shopping put away: Milk 3 seen of 3 bought") and mentions any difference without changing anything. Otherwise it tells you it saw the goods and asks for the receipt or a photo.
- **Uncertain sightings** (below `min_confidence`) wait in the dashboard's Proposals without a message, unless a passage camera sees the same product leave.
- **Zone checks by a camera** are always proposals, and only of what went down, even in automatic mode. Products seen in a larger quantity than recorded are mentioned in the check message.

### List check

Some places are hard to photograph or to see into: a cupboard with stock behind the first row, a full fridge, a freezer. For them, choose the list check in the guided setup ("Go through the list with me"), or give the zone a `ledger` source. Changes come in as usual: receipts, photos you send, and what you say or write ("I put two jars of jam in the cupboard", "I finished the peas").

```yaml
action: quartermaster.set_zone
data:
  zone: Stock cupboard
  sources:
    - type: ledger
      check_days: 30        # 0: only when you ask
```

Such a zone never asks for photos and never shows up as "not updated". Every `check_days`, or with `/check <zone>` on Telegram (`/controlla`), the check button or by voice, Quartermaster sends what should be there, oldest first, with **−1**, **finished** and **All right** buttons; you can also answer in words. By voice the agent reads the five oldest products; say "and then?" ("e poi?") for the next five.

With a camera first (`camera`, then `ledger`), the list replaces the photo request when the frame is unclear, and no periodic list is sent. Set `shelf_life_days` on products that keep long (frozen food, preserves) so purchases without a date get one and the expiry calendar tells you what to use first.

### Show mode

With `active: when_armed` (the default), show mode works only while the zone's **Show mode** switch is on. You turn it on from the dashboard, an automation, or by voice ("I'm taking things from the garage"), and it turns itself off after 10 minutes. Three triggers in a row without any change in the show area suspend it and create a repair issue. Turning it on again resumes it.

Frames show people. Only the show area goes to the AI provider, and frames are deleted right after the analysis.

### Cameras confirming each other

A sighting by a zone camera and one by a passage camera of the same product within the match window (default 5 minutes) combine their confidence. An uncertain take is then asked about. A missing sighting never counts against a product: you may take something from the garage straight to the car.

## Photos

Send a photo through any of these:
- **Telegram**: with the zone name as caption, or pick the zone with the buttons;
- **Dashboard**: the photo card;
- **`quartermaster.analyze_image`**: with a file or a camera.

A photo you send shows the shelf as it is: quantities follow what it shows, higher ones too (a gift, a purchase without receipt). In **confirm** mode (the **Update mode** select), changes become a proposal. Confirm or reject it from the dashboard, Telegram or by voice, correcting product and quantity if needed. A correction teaches Quartermaster the name the model used. In **automatic** mode, changes above the confidence threshold are applied and the rest are proposed. When several products would change by more than half, nothing is applied automatically.

A product recorded in the zone that the picture does not show is listed as **not seen**, at zero. It may only be hidden behind something, so confirming the proposal leaves it as it is: tap its own **<product> finished** button on Telegram, or tick it in the Proposals card, to set it to zero. Otherwise it expires with the proposal. The same holds for camera counts.

A product the inventory does not know yet (the first photo of a new home, a gift) is listed as **unknown product**. Confirming the proposal leaves it waiting: tap its **➕ New <product>** button on Telegram, choose **New product** in the Proposals card, or ask by voice, and it is created with the name the model read (the dashboard and voice can give another name) and the quantity seen, in the zone of the photo. Otherwise it expires with the proposal.

## Receipts and order emails

- **Receipt photo:** send it on Telegram with the caption "receipt" (or press **Receipt** among the zone buttons), choose **A receipt** in the dashboard photo card, or call `quartermaster.analyze_receipt` with a file.
- **Order emails:** set up the IMAP integration for the mailbox that receives order and delivery emails, then choose its entry and the allowed senders (a whole address or a domain) under **Configure → Shopping list**. Only new emails from those senders are read, with the AI Task for text if set.

The purchases always become a proposal: confirm, correct or reject them as for photos. A correction teaches Quartermaster the name on the receipt. Confirmed purchases go to each product's default zone, with a best-before date from its shelf life when known. An unknown product on a receipt waits like one in a photo; its **➕ New** button first asks in which zone it goes, and that becomes its default zone.

## Shopping

- An essential below its minimum goes on the list once, with the quantity up to its target.
- **Go shopping** (button, `quartermaster.start_shopping`, Telegram `/spesa`) sends the list and the suggestions and turns shopping mode on. While it is on, checking an item off records the purchase.
- Manual items are never removed automatically.
- With at least three consumptions in the last 60 days, a product that at the usual pace runs out within the forecast days (5 by default, **Configure → Shopping list**) is suggested; an essential goes on the list before it drops below its minimum.
- To see the list in Bring!, Todoist or another app, set **Copy the shopping list to** in the options. Items you add there yourself are left alone.

## Counting

"I have 3 yogurts left" sets the quantity (a correction in the journal) instead of adding or removing.

## Undo

Every change is in the journal and can be undone:
- "annulla" or "undo" by voice or chat;
- the **Undo** button;
- the History view of the dashboard, operation by operation;
- `quartermaster.undo_last`.

## Backup

Your inventory is in `<config>/quartermaster/pantry.db` and is included in Home Assistant backups. To move it elsewhere, run `quartermaster.export_data`, which writes JSON (everything) or CSV (products and stock) to `<config>/quartermaster/exports/`. `quartermaster.import_data` with a JSON export restores exactly the same state.
