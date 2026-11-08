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
