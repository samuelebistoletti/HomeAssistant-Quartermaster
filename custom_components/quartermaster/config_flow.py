"""Config flow and options flow for Quartermaster."""

from __future__ import annotations

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
    CONF_NON_ADMIN_WRITES,
    CONF_RECORD_PURCHASE_ON_CHECK,
    CONF_SHOPPING_MODE_MINUTES,
    CONF_STALE_ZONE_DAYS,
    CONF_STRUCTURE_MODE,
    CONF_TELEGRAM_BOT,
    CONF_TELEGRAM_CHATS,
    CONF_TELEGRAM_EVENT,
    CONF_TELEGRAM_GROUPS,
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
)
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


class QuartermasterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Quartermaster config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> QuartermasterOptionsFlow:
        """Return the options flow."""
        return QuartermasterOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the setup; the full multi-step flow comes in later phases."""
        if user_input is not None:
            return self.async_create_entry(title="Quartermaster", data={})
        return self.async_show_form(step_id="user")


OPTION_GROUPS: dict[str, tuple[str, ...]] = {
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


def _group_schema(group: str) -> probatio.Schema:
    keys = OPTION_GROUPS[group]
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
