# Changelog

## [Unreleased]

### Added
- Project skeleton: single-instance config flow, English and Italian translations, placeholder brand images, development environment (dev container, Home Assistant and ha-mcp in Docker) and CI (tests against the stable and beta Home Assistant, security, hassfest, Marketplace validation, CodeQL).
- Inventory database (SQLite in the configuration folder, numbered migrations) with zones, products, essentials, stock and a journal of every movement.
- Actions `adjust_stock`, `undo_last`, `set_stock`, `add_product`, `set_essential`, `export_data` and `import_data` (JSON full backup, CSV stock list), with English and Italian texts.
