<p align="center">
  <img src="custom_components/quartermaster/brand/logo.png" alt="Quartermaster" width="460">
</p>

# Quartermaster

An AI agent for Home Assistant that keeps track of your pantry, fridge, freezer and shelves, and fills in the shopping list for you. You talk to it through Assist, Alexa, Telegram, the Home Assistant app and automations; it reads photos, receipts and camera frames through the AI provider you already use in Home Assistant.

> [!WARNING]
> Quartermaster is under development. There is no release yet; the first one will be 1.0.0. The design is in [docs/SPEC.md](docs/SPEC.md).

## Contents

- [What it does](#what-it-does)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Configuration](#configuration)
- [Zones and sources](#zones-and-sources)
- [Channels](#channels)
- [Notifications](#notifications)
- [Actions](#actions)
- [Events](#events)
- [Entities](#entities)
- [Privacy and data](#privacy-and-data)
- [AI providers](#ai-providers)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Backup and removal](#backup-and-removal)
- [Documentation](#documentation)
- [Development](#development)
- [Compatibility](#compatibility)

## What it does

- **Inventory by zone.** Products, quantities per zone, best-before dates and a journal of every change, each one undoable.
- **Essentials and shopping list.** Products that must always be at home go on the shopping list by themselves when they run low. While shopping you get suggestions with a reason ("you have 1, you usually keep 4"). Checking an item off can record the purchase.
- **Talk to it.** "I finished the milk", "do I still have coffee?", "I have 3 yogurts left", "what should I buy?". Through Assist, Alexa, Telegram or the dashboard. With two cheeses in the fridge it asks which one.
- **Photos and cameras.** Send a photo of the fridge or let a camera check a shelf: Quartermaster proposes the changes and you confirm them, or applies them in automatic mode above a confidence threshold. An interior camera can check its zone when the door closes. With **show mode**, a product shown to a zone's camera is recorded as taken. A camera at the entrance can confirm what the zone camera saw.
- **Receipts and order emails.** Photograph a receipt (Telegram, dashboard, action) or let order emails from chosen senders arrive through the IMAP integration: the purchases become a proposal to confirm.
- **Forecast.** From the journal, a product you use regularly is suggested before it runs out ("at the usual pace it runs out in 3 days"); an essential goes straight on the list.
- **Notifications.** Photo requests, expiring products, essentials running out, the list when you go shopping. Sent to the notify entities you choose, with quiet hours, and to Telegram with buttons.

Everything is in English and Italian, voice included.

## How it works

- The inventory lives in a SQLite database in your configuration folder (`<config>/quartermaster/pantry.db`), included in Home Assistant backups.
- Sentences go to **your conversation agent**, which uses the Quartermaster tools (read stock, change it, ask which product you mean, start a check) through the Assist API. Quartermaster has no AI client of its own.
- Photos and camera frames are cropped, checked for light and sharpness, and sent to **your AI Task entity**, which returns the products it sees in a fixed structure. Quartermaster compares them with the inventory and proposes the changes.
- To buy, suggestions and expiring products are recomputed after every change and every hour.
- The dashboard is a separate download (see [Dashboard](#dashboard)) that reads the same data through entities and websocket commands.

## Requirements

- Home Assistant 2026.11.0, the latest release. Older releases are not supported.
- A **conversation agent** with an LLM, from any provider in Home Assistant, for Telegram, Alexa, the dashboard and `quartermaster.ask`. Since 2026.11 an administrator must attach the **Assist** API to it, so it can use the Quartermaster tools.
- An **AI Task** entity with a vision model, for photos, receipts and cameras. Optionally a second AI Task entity for text only (order emails).
- Optional:
  - the [Telegram bot](https://www.home-assistant.io/integrations/telegram_bot/) integration (Polling or Webhooks);
  - cameras, with a motion event or binary sensor for show mode, and a door sensor for interior cameras;
  - the [IMAP](https://www.home-assistant.io/integrations/imap/) integration for order emails;
  - an Alexa custom skill ([guide](docs/alexa.md)).

## Installation

### Marketplace

Once 1.0.0 is released, Quartermaster can be installed from the Marketplace (**Settings** → **Marketplace**). Until it is in the default catalog, add `https://github.com/samuelebistoletti/HomeAssistant-Quartermaster` there as a custom repository of type **Integration**, install **Quartermaster** and restart Home Assistant.

### Manual

Copy `custom_components/quartermaster` from a release ZIP into `<config>/custom_components/quartermaster` and restart Home Assistant.

### Setup

Go to **Settings** → **Devices & services** → **Add integration** → **Quartermaster**. The guided setup asks for:

1. the name and the language of the messages Quartermaster sends;
2. the conversation agent and the AI Task entities;
3. your first zones (Pantry, Fridge and Freezer are suggested), each with an optional camera and crop area;
4. Telegram and voice;
5. notifications;
6. your first essentials, with minimum and target stock.

Every step can be skipped and changed later. Quartermaster has a single entry.

### Dashboard

The dashboard comes from a separate repository, [HomeAssistant-Quartermaster-Dashboard](https://github.com/samuelebistoletti/HomeAssistant-Quartermaster-Dashboard). Install it from the Marketplace as a **Dashboard**, then create a dashboard from **Settings** → **Dashboards** → **Add dashboard** → **Quartermaster**. Home Assistant offers no documented way for the integration to check that the dashboard is installed, so do not skip this step.

## Configuration

Everything is in **Settings** → **Devices & services** → **Quartermaster** → **Configure**, grouped in:

| Group | What you set |
| --- | --- |
| General | Language of the messages Quartermaster sends (notifications, Telegram) |
| AI models | Conversation agent, AI Task entities for images and for text, structured or text output (the fallback for providers that handle nested structures badly), confidence threshold for automatic mode, image analyses per day and per hour |
| Voice and Alexa | How long `quartermaster.ask` keeps a conversation, read-only Alexa |
| Shopping list | Copy to another todo list (Bring!, Todoist, local list…), purchase on check-off, shopping mode duration, stale zone days, expiring threshold, forecast days, order emails (IMAP entry and allowed senders) |
| Telegram | The bot's event entity, the bot, allowed chats, groups |
| Notifications | Notify entities per message type (the first available one is used), quiet hours, Telegram copies with buttons |
| Permissions | The non-admin users who can change data. Administrators always can; reading is open to everyone |

The **Update mode** select chooses between **confirm** (every recognized change becomes a proposal) and **automatic** (changes at or above the confidence threshold are applied, the rest are proposed; when several products would change by more than half, nothing is applied automatically).

## Zones and sources

A zone is a place where things are kept: pantry, fridge, freezer, a garage shelf. Each zone has **sources** in priority order, and a check (scheduled with a cron expression, by voice, from a button or the dashboard) tries them one after the other:

| Source | What it does |
| --- | --- |
| `camera` | Reads a frame, crops it, checks light and sharpness, skips unchanged images and asks the model. An unclear result, or an essential not seen, moves to the next source. With a door sensor it also checks when the door closes. |
| `manual` | Asks you for a photo, with one reminder after 12 hours. |
| `camera_show` | Show mode: a product shown to the camera is recorded as taken (or put back). Off by default. |
| `passage` | A camera where people pass (entrance, yard). Products carried in or out are proposed and confirm what a zone camera saw. It never writes on its own. |

Zones are created in the guided setup, with `quartermaster.add_zone` and `quartermaster.set_zone`, or from the dashboard. Zones without a camera can have `reminder_days`: when they have not been updated for that many days, Quartermaster asks for a photo. The [user guide](docs/user-guide.md) has examples for every source.

## Channels

### Assist and the Home Assistant app

Choose an agent with the Assist API attached in your voice assistant: the Quartermaster tools are available there like any other Home Assistant tool.

### Telegram

1. Set up the [Telegram bot](https://www.home-assistant.io/integrations/telegram_bot/) integration with Polling or Webhooks and allow your chat in it.
2. In **Configure → Telegram**, choose the bot's update event entity. Optionally restrict the chats further and allow groups.

Then:
- write normally ("I finished the milk") and Quartermaster answers through your agent;
- send a photo with the zone name as caption, or without a caption and pick the zone with the buttons;
- send a receipt with the caption "receipt" (in Italian "scontrino"), or press **Receipt** among the zone buttons;
- commands: `/list` (`/lista`), `/shopping` (`/spesa`), `/undo` (`/annulla`);
- proposals and suggestions come with buttons to confirm, reject or add to the list.

A photo request started from a chat goes back to that chat.

### Alexa

A private Alexa custom skill sends what you say to `quartermaster.ask`. Setup, interaction models for Italian and English, and the `intent_script` are in [docs/alexa.md](docs/alexa.md).

### Automations

Every action returns response data, and Quartermaster fires [events](#events) you can trigger on. Ready-made examples are in [`examples/automations`](examples/automations):

| File | What it does |
| --- | --- |
| `weekly_zone_check.yaml` | Checks the pantry every Sunday evening |
| `photo_request_on_tablet.yaml` | Shows photo requests on a wall tablet as a persistent notification |
| `go_shopping_at_the_shop.yaml` | Starts shopping mode when someone arrives at a shop |
| `arm_show_mode_with_garage_door.yaml` | Turns show mode on when the garage door opens |
| `receipt_from_folder.yaml` | Reads receipts saved in a folder watched by Folder Watcher |

## Notifications

Each kind of message has its own list of notify entities, used in order (the first available one gets it):

| Kind | When |
| --- | --- |
| Photo requests | A zone needs a photo (with what to frame), and the reminder |
| Zone check results | A check ended, including failed checks with the reason |
| Expiring products | Products within the expiring threshold |
| Products recognized by cameras | Show mode and passage cameras, with an undo button on Telegram |
| Essentials running out | An essential went on the list |
| Shopping list and suggestions | When you go shopping |
| Weekly summary | Once a week |
| Late voice replies | An answer that took longer than the voice channel could wait |
| Purchases from order emails | A new purchase proposal from an email |

During quiet hours messages wait until the end. With **Also notify Telegram chats**, every message also goes to the allowed private chats, with buttons.

## Actions

All actions are in the `quartermaster` domain. `config_entry_id` is optional: Quartermaster has one entry. Field details are in **Developer tools → Actions**.

| Action | Purpose |
| --- | --- |
| `ask` | Send a sentence to the agent with the Quartermaster tools; returns speech, whether to keep listening and a reprompt (used by Alexa) |
| `adjust_stock` | Consume, buy or finish a product |
| `set_stock` | Set a quantity (counting) |
| `undo_last` | Undo the last operation, or a given one |
| `add_product`, `set_essential` | Products and essentials (minimum and target stock) |
| `add_zone`, `remove_zone`, `set_zone` | Zones, their sources, schedule and thresholds |
| `get_suggestions`, `refresh_shopping_list`, `start_shopping`, `add_to_shopping_list` | Shopping list and suggestions |
| `analyze_image` | Analyze a photo (file or camera) for a zone |
| `analyze_receipt` | Read a receipt photo into a purchase proposal |
| `confirm_pending` | Confirm, correct or reject a proposal |
| `check_zone` | Run a zone check now |
| `arm_show_mode` | Turn show mode on for a zone |
| `import_from_todo` | Import from a todo list, with a preview |
| `export_data`, `import_data` | JSON backup, CSV stock list |

Writing actions called by a user follow the [permissions](#configuration); automations and scripts without a user are always allowed.

## Events

| Event | Fired when | Data |
| --- | --- | --- |
| `quartermaster_photo_request` | A zone needs a photo | `request_id`, `zone_id`, `zone`, `reason`, `channel`, `reminder`, `products`, `chat_id` |
| `quartermaster_zone_checked` | A zone check ended | `zone_id`, `status`, `reason`, `source`, `overall_confidence` and the result |
| `quartermaster_show_recognized` | Show mode recognized a product | `zone_id`, `zone`, `product`, `action`, `quantity`, `operation_id`, `to_list`, `announce` |
| `quartermaster_purchases_proposed` | A receipt or an email became a proposal | `proposal_id`, `source`, `count`, `channel` |
| `quartermaster_ask_result` | A late `quartermaster.ask` answer is ready | `channel`, `session_key` |

## Entities

- **Shopping list** (todo).
- **Sensors:** to buy, suggestions (with the reason), expiring products, pending proposals, photo requests, analyses today.
- **Calendar:** best-before dates.
- **Buttons:** Go shopping, Undo.
- **Select:** update mode.
- **Per zone:** last check sensor, zone error binary sensor and a check button. Zones with show mode also get a show mode switch and a last shown sensor.

Per-zone entities appear and disappear with their zone, without a reload.

## Privacy and data

- **What goes to your AI provider:** the cropped area of a photo or frame, the product names it needs to match, and the sentences you say or write. Text in images and emails is treated as data, never as instructions.
- **Images:** a photo is written to `<media>/quartermaster/tmp` only for the duration of the call and deleted right after. The last crop of each zone and the crops of open proposals are kept in `<config>/quartermaster/crops` (outside the media browser) for the dashboard, and deleted when the proposal is decided or expires. Show mode and passage frames are never kept.
- **Show mode and passage cameras** see people. They are off by default, send only the set area, and the dashboard asks for an explicit acknowledgement before adding them.
- **Order emails:** only new emails from the senders you allow, from the IMAP entry you choose, are read.
- **Diagnostics** contain counts and settings only; the chosen entities, chats, users and senders are redacted.
- **Limits:** image analyses per day and per hour, and at most 20 `quartermaster.ask` requests per channel and minute.

## AI providers

Quartermaster works with any provider integrated in Home Assistant, local ones included, as long as its conversation agent supports LLM tools and its AI Task entity supports image attachments. If a provider handles nested structures badly, set **Structured output** to the text fallback. Every request to the agent carries the Assist instructions, the tools and the entities exposed to Assist: on a provider that charges per token or has a daily allowance, exposing fewer entities to Assist lowers the cost of each sentence. A table of tested providers and their results will be added with the 1.0 tests.

## Limitations

- In the Home Assistant Android app the photo field of the dashboard opens the gallery only ([home-assistant/android#6055](https://github.com/home-assistant/android/issues/6055)); Telegram is the quickest way to take a photo.
- Talking to Alexa without saying the skill name is not available in Italian: use Alexa routines for fixed phrases.
- Quantities are not converted between units.
- Interior cameras checked when a door closes, show mode and passage cameras count against the image analyses per day and per hour.
- After adding or deleting a zone, reload the dashboard to see its section.

## Troubleshooting

- **The agent does not use Quartermaster.** Attach the Assist API to the conversation agent (administrator only) and choose that agent in **Configure → AI models**.
- **Photos are never recognized.** The AI Task entity needs a model with vision. Check the **Analyses today** sensor against the daily limit, and try the text fallback for structured output.
- **Telegram does not answer.** Check that the chat is allowed in the Telegram bot integration and, if set, in **Configure → Telegram**; group messages need **Groups** on.
- **Show mode stopped.** Three triggers in a row without a change suspend it and create a repair issue; turning it on again resumes it.
- **Logs:** add this to `configuration.yaml` and restart:

  ```yaml
  logger:
    logs:
      custom_components.quartermaster: debug
  ```

- **Diagnostics:** **Settings** → **Devices & services** → **Quartermaster** → **⋮** → **Download diagnostics**. Check the file, then attach it to an [issue](https://github.com/samuelebistoletti/HomeAssistant-Quartermaster/issues).

## Backup and removal

The inventory is in `<config>/quartermaster/pantry.db` and is included in Home Assistant backups. `quartermaster.export_data` writes a JSON backup (everything) or a CSV stock list to `<config>/quartermaster/exports/`; `quartermaster.import_data` restores a JSON export exactly.

To remove Quartermaster:

1. **Settings** → **Devices & services** → **Quartermaster** → **⋮** → **Delete**.
2. Remove the integration from the Marketplace (or delete `<config>/custom_components/quartermaster`).
3. Optional: delete `<config>/quartermaster/` (database, crops and exports). It is kept so that a reinstall finds your data.

## Documentation

| Document | Content |
| --- | --- |
| [User guide](docs/user-guide.md) | First steps, zones and sources, photos, receipts, shopping, counting, undo, backup |
| [Alexa](docs/alexa.md) | Custom skill setup in Italian and English |
| [Dashboard API](docs/dashboard-api.md) | Websocket commands and entities used by the dashboard |
| [Specification](docs/SPEC.md) | The design (Italian) |
| [Technical notes](TECHNICAL_NOTES.md) | Decisions, deviations from the specification and their sources |
| [Changelog](CHANGELOG.md) | Changes by release |
| [Contributing](CONTRIBUTING.md) | Development environment, tests and pull requests |
| [Security](SECURITY.md) | How to report a vulnerability |

## Development

The development environment (Home Assistant and the [ha-mcp](https://github.com/homeassistant-ai/ha-mcp) server in Docker, tests, lint, type check), including how to set it up on another computer, is described in [CONTRIBUTING.md](CONTRIBUTING.md). In short:

```bash
python3.14 -m venv .venv && .venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest        # tests with a 90 % coverage gate
.venv/bin/mypy                    # strict type check
bash scripts/lint                 # ruff format and check
docker compose up -d              # dev Home Assistant on http://localhost:8123
```

## Compatibility

| Quartermaster | Quartermaster Dashboard | Home Assistant |
| --- | --- | --- |
| unreleased (dashboard API 1) | unreleased | 2026.11.0 |

## License

[MIT](LICENSE)
