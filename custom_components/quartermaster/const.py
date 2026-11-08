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
CONF_TELEGRAM_EVENT: Final = "telegram_event_entity"
CONF_TELEGRAM_BOT: Final = "telegram_bot_entry"
CONF_TELEGRAM_CHATS: Final = "telegram_chats"
CONF_TELEGRAM_GROUPS: Final = "telegram_groups"

# Language of the phrases sent by the integration (notifications, Telegram).
CONF_LANGUAGE: Final = "language"
SUPPORTED_LANGUAGES: Final = ("en", "it")

# What the guided setup asks for, kept in entry.data until the first setup
# applies it to the database.
DATA_INITIAL_ZONES: Final = "initial_zones"
DATA_INITIAL_ESSENTIALS: Final = "initial_essentials"
DATA_INITIAL_IMPORT: Final = "initial_import"

# Forecast of consumption (spec §15): days ahead, 0 turns it off.
CONF_FORECAST_DAYS: Final = "forecast_days"
DEFAULT_FORECAST_DAYS: Final = 5

# AI Task for text-only work (order emails); the image one when empty.
CONF_AI_TASK_TEXT: Final = "ai_task_text"
CONF_HOURLY_ANALYSIS_LIMIT: Final = "hourly_analysis_limit"
DEFAULT_HOURLY_ANALYSIS_LIMIT: Final = 30

# Order emails through the IMAP integration (spec §15).
CONF_EMAIL_ENTRY: Final = "email_entry"
CONF_EMAIL_SENDERS: Final = "email_senders"

# Requests to quartermaster.ask per channel and minute (spec §8).
ASK_PER_MINUTE: Final = 20
