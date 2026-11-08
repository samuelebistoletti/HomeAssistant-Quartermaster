# Changelog

## [Unreleased]

### Added
- Project skeleton: single-instance config flow, English and Italian translations, placeholder brand images, development environment (dev container, Home Assistant and ha-mcp in Docker) and CI (tests against the stable and beta Home Assistant, security, hassfest, Marketplace validation, CodeQL).
- Inventory database (SQLite in the configuration folder, numbered migrations) with zones, products, essentials, stock and a journal of every movement.
- Actions `add_zone`, `remove_zone`, `adjust_stock`, `undo_last`, `set_stock`, `add_product`, `set_essential`, `export_data` and `import_data` (JSON full backup, CSV stock list), with English and Italian texts. Undo reverts a whole operation, even when it changed several products.
- Shopping list todo entity with automatic items for essentials below minimum, manual and suggested items, optional purchase recording when checked, one-way copy to another todo list and import from a todo list.
- Sensors for essentials to buy, purchase suggestions (with the reason) and expiring products; best-before calendar; Go shopping and Undo buttons; update mode select; options flow.
- Actions `get_suggestions`, `refresh_shopping_list`, `start_shopping`, `add_to_shopping_list` and `import_from_todo`.
- LLM tools for conversation agents using the Assist API (stock, missing, suggestions, expiring, shopping list, adjust stock with clarifications, undo, essentials and new products with confirmation).
- Action `ask` for Alexa and other channels: sessions per channel with clarifications, reply or acknowledgement within 5 seconds, reprompt; options for the agent, session length and read-only Alexa.
- Image analysis of zones with any AI Task entity: quality checks, crop, resize and metadata removal with Pillow, skip of unchanged images, structured or text output, validation.
- Proposals of changes with confirmation, corrections and alias learning; automatic mode above a confidence threshold; actions `analyze_image` and `confirm_pending`, LLM tool to confirm; sensors for pending proposals and daily analyses.
- Websocket commands for the dashboard (snapshot, live updates, journal, photo upload) and a write permission rule for non-admin users; undo of a given operation.
- Zone manager: sources in priority order (camera with crop and daily limit, manual), zone checks with fallback to a photo request, cron schedules, reminders for zones without camera, photo requests with one reminder and expiry; actions `set_zone` and `check_zone`, LLM tool to check a zone; per-zone last check sensor, zone error binary sensor and check button, photo requests sensor.
- camera_show: products shown to a zone camera recorded as consumption (or returned), burst capture with sharpness, change and duplicate checks, suspension with a repair issue; show mode switch per zone, last shown sensor, `arm_show_mode` action and LLM tool.
- Passage cameras: products carried in or out are proposed, and corroborate zone camera sightings without double counting.
- Confirming only some items of a proposal keeps the others pending; an accepted below-target suggestion buys up to the target; the dashboard snapshot includes zone checks, errors, show mode and photo requests.
- Telegram channel through the Telegram bot integration: texts to the agent per chat, photos to the vision pipeline with zone from the caption or buttons, proposal and suggestion buttons, undo, commands, allowed chats, group option and rate limit.
- Notifications per message type with fallback entities, quiet hours and optional Telegram copies with buttons: photo requests, zone checks, expiring products (daily), camera recognitions with Undo, essentials running out, shopping list and suggestions, weekly summary, late voice replies.
- Alexa guide (`docs/alexa.md`) and examples: multilingual interaction models and `intent_script` for `quartermaster.ask`.
- Guided setup: name and language, AI models, first zones with optional camera and crop, Telegram and voice, notifications, first essentials and an optional import from a todo list.
- Language option for the messages sent by Quartermaster.
- Options grouped in a menu (general, voice and Alexa, shopping list, Telegram, notifications, permissions).
- Diagnostics with personal data redacted (only counts and settings).
- Receipts: `analyze_receipt` action, Telegram caption or button, dashboard upload; purchases from order emails through the IMAP integration. Both become purchase proposals.
- Consumption forecast: suggestion reason "running out" and essentials on the list before they run low.
- Interior cameras checked when their door closes; shelf life sets the best-before date of purchases.
- Kept crops for proposals and zones, served to the dashboard; zone settings in the snapshot for zone editing.
- AI Task for text, hourly analysis limit, per-minute limit for `ask`, 15 s camera timeout, tool argument validation, "counted" quantities by voice, photo requests that say what to frame, per-zone announcements, failed checks announced.
- Automation examples.
- README and user guide.
