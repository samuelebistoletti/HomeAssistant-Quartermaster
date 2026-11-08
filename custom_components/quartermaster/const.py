"""Constants for the Quartermaster integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "quartermaster"

# Folder under <config>/quartermaster for export and import files.
TRANSFER_DIR: Final = "exports"
DATABASE_FILE: Final = "pantry.db"

# Options (entry.options) and their defaults.
CONF_EXTERNAL_TODO: Final = "external_todo"
CONF_RECORD_PURCHASE_ON_CHECK: Final = "record_purchase_on_check"
CONF_SHOPPING_MODE_MINUTES: Final = "shopping_mode_minutes"
CONF_STALE_ZONE_DAYS: Final = "stale_zone_days"
CONF_EXPIRING_DAYS: Final = "expiring_days"

DEFAULT_RECORD_PURCHASE_ON_CHECK: Final = False
DEFAULT_SHOPPING_MODE_MINUTES: Final = 120
DEFAULT_STALE_ZONE_DAYS: Final = 7
DEFAULT_EXPIRING_DAYS: Final = 3

# Internal settings stored in the database.
SETTING_UPDATE_MODE: Final = "update_mode"
