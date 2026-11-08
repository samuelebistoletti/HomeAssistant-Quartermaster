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
CONF_AGENT_ID: Final = "agent_id"
CONF_ASK_SESSION_SECONDS: Final = "ask_session_seconds"
CONF_ALEXA_READ_ONLY: Final = "alexa_read_only"

DEFAULT_ASK_SESSION_SECONDS: Final = 120
DEFAULT_ALEXA_READ_ONLY: Final = False
CONF_AI_TASK_IMAGE: Final = "ai_task_image"
CONF_STRUCTURE_MODE: Final = "structure_mode"
CONF_DAILY_ANALYSIS_LIMIT: Final = "daily_analysis_limit"
CONF_CONFIDENCE_THRESHOLD: Final = "confidence_threshold"

DEFAULT_STRUCTURE_MODE: Final = "nested"
DEFAULT_DAILY_ANALYSIS_LIMIT: Final = 50
DEFAULT_CONFIDENCE_THRESHOLD: Final = 0.75

SETTING_ANALYSES_DAY: Final = "analyses_day"
SETTING_ANALYSES_COUNT: Final = "analyses_count"
CONF_NON_ADMIN_WRITES: Final = "non_admin_writes"
DEFAULT_NON_ADMIN_WRITES: Final = False
