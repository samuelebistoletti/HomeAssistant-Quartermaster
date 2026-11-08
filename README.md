<p align="center">
  <img src="custom_components/quartermaster/brand/logo.png" alt="Quartermaster" width="460">
</p>

# Quartermaster

An AI agent for Home Assistant that keeps track of your pantry, fridge, freezer and shelves, and fills in the shopping list for you. You talk to it through Assist, Alexa, Telegram, the Home Assistant app and automations; it reads photos and camera frames through the AI provider you already use in Home Assistant.

> [!WARNING]
> Quartermaster is under development. There is no release yet; the first one will be 1.0.0. The design is in [docs/SPEC.md](docs/SPEC.md).

## What it does

- **Inventory by zone.** Products, quantities per zone, best-before dates and a journal of every change, each one undoable.
- **Essentials and shopping list.** Products that must always be at home go on the shopping list by themselves when they run low. While shopping you get suggestions with a reason ("you have 1, you usually keep 4"). Checking an item off can record the purchase.
- **Talk to it.** "I finished the milk", "do I still have coffee?", "what should I buy?". Through Assist, Alexa, Telegram or the dashboard. With two cheeses in the fridge it asks which one.
- **Photos and cameras.** Send a photo of the fridge or let a camera check a shelf: Quartermaster proposes the changes and you confirm them, or applies them in automatic mode above a confidence threshold. With **show mode**, a product shown to a zone's camera is recorded as taken. A camera at the entrance can confirm what the zone camera saw.
- **Receipts and order emails.** Photograph a receipt (Telegram caption "receipt", dashboard, action) or let order emails from chosen senders arrive through the IMAP integration: the purchases become a proposal to confirm.
- **Forecast.** From the journal, a product you use regularly is suggested before it runs out ("at the usual pace it runs out in 3 days"); an essential goes straight on the list.
- **Notifications.** Photo requests, expiring products, essentials running out, the list when you go shopping. Sent to the notify entities you choose, with quiet hours, and to Telegram with buttons.

Everything is in English and Italian, voice included.

## Requirements

- Home Assistant 2026.11.0 or later.
- A **conversation agent** with an LLM for Telegram, Alexa and `quartermaster.ask`, from any provider in Home Assistant. Since 2026.11 an administrator must attach the **Assist** API to it, so it can use the Quartermaster tools.
- An **AI Task** entity with a vision model for photos and cameras.
- Optional: the Telegram bot integration (Polling or Webhooks), cameras with a motion event or binary sensor, interior cameras with a door sensor, the IMAP integration for order emails, an Alexa custom skill ([guide](docs/alexa.md)).

## Installation

Once 1.0.0 is released, Quartermaster can be installed from the Marketplace (**Settings** → **Marketplace**). Until it is in the default catalog, add this repository there as a custom repository of type **Integration**.

Then go to **Settings** → **Devices & services** → **Add integration** → **Quartermaster**. The setup walks you through the name and language, the AI models, your first zones (Pantry, Fridge and Freezer are suggested, each with an optional camera), Telegram and voice, notifications and your first essentials. Every step can be skipped and changed later.

The dashboard comes from a separate repository, [HomeAssistant-Quartermaster-Dashboard](https://github.com/samuelebistoletti/HomeAssistant-Quartermaster-Dashboard). Install it from the Marketplace as a **Dashboard**, then create a dashboard from **Settings** → **Dashboards** → **Add dashboard** → **Quartermaster**. Home Assistant offers no documented way for the integration to check that the dashboard is installed, so do not skip this step.

## Configuration

Everything is in **Settings** → **Devices & services** → **Quartermaster** → **Configure**, grouped in:

| Group | What you set |
| --- | --- |
| General | Language of the messages Quartermaster sends (notifications, Telegram) |
| AI models | Conversation agent, AI Task entities for images and for text, structured or text output, confidence threshold for automatic mode, image analyses per day and per hour |
| Voice and Alexa | How long `quartermaster.ask` keeps a conversation, read-only Alexa |
| Shopping list | Copy to another todo list (Bring!, Todoist, local list…), purchase on check-off, shopping mode duration, stale zone days, expiring threshold, forecast days, order emails (IMAP entry and allowed senders) |
| Telegram | The bot's event entity, the bot, allowed chats, groups |
| Notifications | Notify entities per message type (first available wins), quiet hours, Telegram copies with buttons |
| Permissions | Which non-admin users can change data (administrators always can; reading is open to all) |

Zones, their cameras and schedules are set with the `quartermaster.set_zone` action or from the dashboard. See the [user guide](docs/user-guide.md).

## Actions

All actions are in the `quartermaster` domain. `config_entry_id` is optional: Quartermaster has one entry.

| Action | Purpose |
| --- | --- |
| `ask` | Send a sentence to the agent with the Quartermaster tools; returns speech, whether to keep listening and a reprompt (used by Alexa) |
| `adjust_stock`, `set_stock`, `undo_last` | Consume, buy, finish, count, undo (the last operation or a given one) |
| `add_product`, `set_essential`, `add_zone`, `remove_zone`, `set_zone` | Products, essentials, zones and their sources |
| `get_suggestions`, `refresh_shopping_list`, `start_shopping`, `add_to_shopping_list` | Shopping list and suggestions |
| `analyze_image`, `analyze_receipt`, `confirm_pending`, `check_zone`, `arm_show_mode` | Photos, receipts, proposals, zone checks, show mode |
| `import_from_todo`, `export_data`, `import_data` | Import from a todo list, JSON backup, CSV stock list |

Reading and analysis actions return response data, so you can use them in scripts and automations. Examples are in [`examples/automations`](examples/automations).

## Entities

Shopping list (todo); sensors for to buy, suggestions, expiring products, pending proposals, photo requests and analyses today; best-before calendar; Go shopping and Undo buttons; update mode select. Per zone: last check sensor, zone error binary sensor and a check button. Zones with show mode also get a show mode switch and a last shown sensor.

## Providers

Quartermaster has no provider client: it uses the AI Task entity and the conversation agent you choose. Any provider integrated in Home Assistant works if it supports AI Task (with image attachments) and LLM tools, local ones included. A table of tested providers and their results will be added with the 1.0 tests.

## Limitations

- In the Home Assistant Android app the photo field of the dashboard opens the gallery only ([home-assistant/android#6055](https://github.com/home-assistant/android/issues/6055)); Telegram is the quickest way to take a photo.
- Interaction with Alexa without the skill name is not available in Italian: use Alexa routines for fixed phrases.
- Quantities are not converted between units.
- Interior cameras checked when a door closes, show mode and passage cameras count against the image analyses per day and per hour.
- Show mode sends camera frames of the show area to your AI provider. It is off by default; enable it per zone only if you accept that.

## Removal

1. **Settings** → **Devices & services** → **Quartermaster** → **⋮** → **Delete**.
2. Remove the integration from the Marketplace.
3. Optional: delete `<config>/quartermaster/` (the inventory database and exports). It is kept so that a reinstall finds your data.

## Compatibility

| Quartermaster | Quartermaster Dashboard | Home Assistant |
| --- | --- | --- |
| unreleased (dashboard API 1) | unreleased | 2026.11.0+ |

## License

[MIT](LICENSE)
