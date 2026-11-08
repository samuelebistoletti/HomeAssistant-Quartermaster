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
    CONF_ALEXA_READ_ONLY,
    CONF_ASK_SESSION_SECONDS,
    CONF_EXPIRING_DAYS,
    CONF_EXTERNAL_TODO,
    CONF_RECORD_PURCHASE_ON_CHECK,
    CONF_SHOPPING_MODE_MINUTES,
    CONF_STALE_ZONE_DAYS,
    DEFAULT_ALEXA_READ_ONLY,
    DEFAULT_ASK_SESSION_SECONDS,
    DEFAULT_EXPIRING_DAYS,
    DEFAULT_RECORD_PURCHASE_ON_CHECK,
    DEFAULT_SHOPPING_MODE_MINUTES,
    DEFAULT_STALE_ZONE_DAYS,
    DOMAIN,
)


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


class QuartermasterOptionsFlow(OptionsFlowWithReload):
    """Shopping list options; the entry reloads when they change."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
