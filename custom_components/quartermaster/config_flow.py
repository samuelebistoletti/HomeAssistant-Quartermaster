"""Config flow and options flow for Quartermaster."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
import probatio

from .const import (
    CONF_AGENT_ID,
    CONF_AI_TASK_IMAGE,
    CONF_ALEXA_READ_ONLY,
    CONF_ASK_SESSION_SECONDS,
    CONF_CONFIDENCE_THRESHOLD,
    CONF_DAILY_ANALYSIS_LIMIT,
    CONF_EXPIRING_DAYS,
    CONF_EXTERNAL_TODO,
    CONF_LANGUAGE,
    CONF_NON_ADMIN_WRITES,
    CONF_RECORD_PURCHASE_ON_CHECK,
    CONF_SHOPPING_MODE_MINUTES,
    CONF_STALE_ZONE_DAYS,
    CONF_STRUCTURE_MODE,
    CONF_TELEGRAM_BOT,
    CONF_TELEGRAM_CHATS,
    CONF_TELEGRAM_EVENT,
    CONF_TELEGRAM_GROUPS,
    DATA_INITIAL_ESSENTIALS,
    DATA_INITIAL_IMPORT,
    DATA_INITIAL_ZONES,
    DEFAULT_ALEXA_READ_ONLY,
    DEFAULT_ASK_SESSION_SECONDS,
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_DAILY_ANALYSIS_LIMIT,
    DEFAULT_EXPIRING_DAYS,
    DEFAULT_NON_ADMIN_WRITES,
    DEFAULT_RECORD_PURCHASE_ON_CHECK,
    DEFAULT_SHOPPING_MODE_MINUTES,
    DEFAULT_STALE_ZONE_DAYS,
    DEFAULT_STRUCTURE_MODE,
    DOMAIN,
    SUPPORTED_LANGUAGES,
)
from .core.inventory import InventoryError
from .core.phrases import async_phrase
from .core.shopping import ImportTarget
from .core.zones import validate_sources
from .notify import CONF_QUIET_HOURS, CONF_TELEGRAM_NOTIFY, MessageType, option_key


def _days(maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=maximum,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="d",
        )
    )


OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_LANGUAGE): selector.LanguageSelector(
            selector.LanguageSelectorConfig(
                languages=list(SUPPORTED_LANGUAGES), native_name=True
            )
        ),
        probatio.Optional(CONF_AGENT_ID): selector.ConversationAgentSelector(),
        probatio.Required(
            CONF_ASK_SESSION_SECONDS, default=DEFAULT_ASK_SESSION_SECONDS
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=30,
                max=900,
                step=10,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        probatio.Required(
            CONF_ALEXA_READ_ONLY, default=DEFAULT_ALEXA_READ_ONLY
        ): selector.BooleanSelector(),
        probatio.Optional(CONF_AI_TASK_IMAGE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="ai_task")
        ),
        probatio.Required(
            CONF_STRUCTURE_MODE, default=DEFAULT_STRUCTURE_MODE
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["nested", "text"], translation_key="structure_mode"
            )
        ),
        probatio.Required(
            CONF_CONFIDENCE_THRESHOLD, default=DEFAULT_CONFIDENCE_THRESHOLD
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.5, max=1, step=0.05, mode=selector.NumberSelectorMode.SLIDER
            )
        ),
        probatio.Required(
            CONF_DAILY_ANALYSIS_LIMIT, default=DEFAULT_DAILY_ANALYSIS_LIMIT
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1, max=1000, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        probatio.Required(
            CONF_NON_ADMIN_WRITES, default=DEFAULT_NON_ADMIN_WRITES
        ): selector.BooleanSelector(),
        probatio.Optional(CONF_TELEGRAM_EVENT): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="event", integration="telegram_bot")
        ),
        probatio.Optional(CONF_TELEGRAM_BOT): selector.ConfigEntrySelector(
            selector.ConfigEntrySelectorConfig(integration="telegram_bot")
        ),
        probatio.Optional(CONF_TELEGRAM_CHATS): selector.TextSelector(
            selector.TextSelectorConfig(multiple=True)
        ),
        probatio.Required(
            CONF_TELEGRAM_GROUPS, default=False
        ): selector.BooleanSelector(),
        **{
            probatio.Optional(option_key(kind)): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="notify", multiple=True)
            )
            for kind in MessageType
        },
        probatio.Optional(CONF_QUIET_HOURS): selector.TextSelector(),
        probatio.Required(
            CONF_TELEGRAM_NOTIFY, default=False
        ): selector.BooleanSelector(),
        probatio.Optional(CONF_EXTERNAL_TODO): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="todo")
        ),
        probatio.Required(
            CONF_RECORD_PURCHASE_ON_CHECK, default=DEFAULT_RECORD_PURCHASE_ON_CHECK
        ): selector.BooleanSelector(),
        probatio.Required(
            CONF_SHOPPING_MODE_MINUTES, default=DEFAULT_SHOPPING_MODE_MINUTES
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=10,
                max=720,
                step=5,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="min",
            )
        ),
        probatio.Required(CONF_STALE_ZONE_DAYS, default=DEFAULT_STALE_ZONE_DAYS): _days(
            90
        ),
        probatio.Required(CONF_EXPIRING_DAYS, default=DEFAULT_EXPIRING_DAYS): _days(30),
    }
)


INITIAL_ZONE_KEYS = ("zone_pantry", "zone_fridge", "zone_freezer")
CONF_NAME = "name"
CONF_ZONES = "zones"
CONF_CAMERA = "camera"
CONF_ROI = "roi"
CONF_ESSENTIALS = "essentials"
CONF_IMPORT_TODO = "import_todo"
CONF_IMPORT_TARGET = "import_target"

ZONE_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_CAMERA): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="camera")
        ),
        probatio.Optional(CONF_ROI): selector.TextSelector(),
    }
)

ESSENTIALS_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_ESSENTIALS): selector.TextSelector(
            selector.TextSelectorConfig(multiple=True)
        ),
        probatio.Optional(CONF_IMPORT_TODO): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="todo")
        ),
        probatio.Required(
            CONF_IMPORT_TARGET, default=ImportTarget.ESSENTIALS.value
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[target.value for target in ImportTarget],
                translation_key="import_target",
            )
        ),
    }
)


def parse_roi(text: str) -> list[float]:
    """Parse "x1, y1, x2, y2" fractions; raise ValueError when invalid."""
    try:
        roi = [float(part) for part in text.replace(",", " ").split()]
        validate_sources([{"type": "camera", "entity_id": "camera.x", "roi": roi}])
    except (ValueError, InventoryError) as err:
        raise ValueError(text) from err
    return roi


class QuartermasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Guided setup: home, models, zones, channels, notifications, essentials."""

    VERSION = 1

    def __init__(self) -> None:
        """Start with nothing chosen."""
        self._title = "Quartermaster"
        self._options: dict[str, Any] = {}
        self._pending_zones: list[str] = []
        self._zones: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> QuartermasterOptionsFlow:
        """Return the options flow."""
        return QuartermasterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Name of the home and language of messages."""
        if user_input is not None:
            self._title = user_input[CONF_NAME].strip() or self._title
            self._options[CONF_LANGUAGE] = user_input[CONF_LANGUAGE]
            return await self.async_step_models()
        language = self.hass.config.language
        schema = probatio.Schema(
            {
                probatio.Required(
                    CONF_NAME, default=self.hass.config.location_name or self._title
                ): selector.TextSelector(),
                probatio.Required(
                    CONF_LANGUAGE,
                    default=language if language in SUPPORTED_LANGUAGES else "en",
                ): selector.LanguageSelector(
                    selector.LanguageSelectorConfig(
                        languages=list(SUPPORTED_LANGUAGES), native_name=True
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, last_step=False)

    async def async_step_models(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Conversation agent and AI Task for images."""
        return await self._async_options_step(
            "models", user_input, self.async_step_zones
        )

    async def async_step_zones(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First zones: the usual three, renamed, removed or added to."""
        if user_input is not None:
            names = [" ".join(name.split()) for name in user_input.get(CONF_ZONES, [])]
            self._pending_zones = list(dict.fromkeys(name for name in names if name))
            return await self.async_step_zone()
        language = self._options[CONF_LANGUAGE]
        defaults = [
            await async_phrase(self.hass, language, key) for key in INITIAL_ZONE_KEYS
        ]
        schema = probatio.Schema(
            {
                probatio.Optional(
                    CONF_ZONES, default=defaults
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=defaults, multiple=True, custom_value=True
                    )
                )
            }
        )
        return self.async_show_form(
            step_id="zones", data_schema=schema, last_step=False
        )

    async def async_step_zone(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the camera and crop of each chosen zone, one at a time."""
        if not self._pending_zones:
            return await self.async_step_channels()
        name = self._pending_zones[0]
        errors: dict[str, str] = {}
        if user_input is not None:
            zone: dict[str, Any] = {"name": name}
            if camera := user_input.get(CONF_CAMERA):
                zone["camera"] = camera
                if text := (user_input.get(CONF_ROI) or "").strip():
                    try:
                        zone["roi"] = parse_roi(text)
                    except ValueError:
                        errors[CONF_ROI] = "invalid_roi"
            if not errors:
                self._zones.append(zone)
                self._pending_zones.pop(0)
                return await self.async_step_zone()
        return self.async_show_form(
            step_id="zone",
            data_schema=self.add_suggested_values_to_schema(
                ZONE_SCHEMA, user_input or {}
            ),
            errors=errors,
            description_placeholders={"zone": name},
            last_step=False,
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Telegram and voice."""
        return await self._async_options_step(
            "channels", user_input, self.async_step_notifications
        )

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Notify entities per message type and quiet hours."""
        return await self._async_options_step(
            "notifications", user_input, self.async_step_essentials
        )

    async def async_step_essentials(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """First essentials and an optional import from a todo list."""
        if user_input is None:
            return self.async_show_form(
                step_id="essentials", data_schema=ESSENTIALS_SCHEMA
            )
        data: dict[str, Any] = {}
        if self._zones:
            data[DATA_INITIAL_ZONES] = self._zones
        essentials = [
            " ".join(name.split()) for name in user_input.get(CONF_ESSENTIALS, [])
        ]
        if essentials := [name for name in essentials if name]:
            data[DATA_INITIAL_ESSENTIALS] = essentials
        if todo := user_input.get(CONF_IMPORT_TODO):
            data[DATA_INITIAL_IMPORT] = {
                "entity_id": todo,
                "target": user_input[CONF_IMPORT_TARGET],
            }
        return self.async_create_entry(
            title=self._title, data=data, options=self._options
        )

    async def _async_options_step(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        next_step: Callable[[], Awaitable[ConfigFlowResult]],
    ) -> ConfigFlowResult:
        """Show a step whose fields are entry options, as in the options flow."""
        if user_input is not None:
            self._options.update(user_input)
            return await next_step()
        return self.async_show_form(
            step_id=step_id, data_schema=_setup_schema(step_id), last_step=False
        )


OPTION_GROUPS: dict[str, tuple[str, ...]] = {
    "general": (CONF_LANGUAGE,),
    "models": (
        CONF_AGENT_ID,
        CONF_AI_TASK_IMAGE,
        CONF_STRUCTURE_MODE,
        CONF_CONFIDENCE_THRESHOLD,
        CONF_DAILY_ANALYSIS_LIMIT,
    ),
    "voice": (CONF_ASK_SESSION_SECONDS, CONF_ALEXA_READ_ONLY),
    "shopping": (
        CONF_EXTERNAL_TODO,
        CONF_RECORD_PURCHASE_ON_CHECK,
        CONF_SHOPPING_MODE_MINUTES,
        CONF_STALE_ZONE_DAYS,
        CONF_EXPIRING_DAYS,
    ),
    "telegram": (
        CONF_TELEGRAM_EVENT,
        CONF_TELEGRAM_BOT,
        CONF_TELEGRAM_CHATS,
        CONF_TELEGRAM_GROUPS,
    ),
    "notifications": (
        *(option_key(kind) for kind in MessageType),
        CONF_QUIET_HOURS,
        CONF_TELEGRAM_NOTIFY,
    ),
    "permissions": (CONF_NON_ADMIN_WRITES,),
}


# Setup steps that take their fields from the option groups.
SETUP_STEPS: dict[str, tuple[str, ...]] = {
    "models": OPTION_GROUPS["models"],
    "channels": OPTION_GROUPS["telegram"] + OPTION_GROUPS["voice"],
    "notifications": OPTION_GROUPS["notifications"],
}


def _setup_schema(step_id: str) -> probatio.Schema:
    return _keys_schema(SETUP_STEPS[step_id])


def _group_schema(group: str) -> probatio.Schema:
    return _keys_schema(OPTION_GROUPS[group])


def _keys_schema(keys: tuple[str, ...]) -> probatio.Schema:
    return probatio.Schema(
        {key: value for key, value in OPTIONS_SCHEMA.schema.items() if str(key) in keys}
    )


class QuartermasterOptionsFlow(OptionsFlowWithReload):
    """Options grouped in a menu; the entry reloads when they change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the option groups."""
        return self.async_show_menu(step_id="init", menu_options=list(OPTION_GROUPS))

    async def _async_group(
        self, group: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # Fields left empty in this group are removed from the options.
            kept = {
                key: value
                for key, value in self.config_entry.options.items()
                if key not in OPTION_GROUPS[group]
            }
            return self.async_create_entry(data=kept | user_input)
        return self.async_show_form(
            step_id=group,
            data_schema=self.add_suggested_values_to_schema(
                _group_schema(group), self.config_entry.options
            ),
        )

    async def async_step_general(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Language of messages."""
        return await self._async_group("general", user_input)

    async def async_step_models(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Agent, AI Task and image analysis options."""
        return await self._async_group("models", user_input)

    async def async_step_voice(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """quartermaster.ask and Alexa options."""
        return await self._async_group("voice", user_input)

    async def async_step_shopping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Shopping list and suggestion options."""
        return await self._async_group("shopping", user_input)

    async def async_step_telegram(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Telegram channel options."""
        return await self._async_group("telegram", user_input)

    async def async_step_notifications(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Notification options."""
        return await self._async_group("notifications", user_input)

    async def async_step_permissions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Write permission options."""
        return await self._async_group("permissions", user_input)
