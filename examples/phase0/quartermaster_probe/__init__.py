"""Dev-only probe: contributes one fake LLM tool to check tool calling (Phase 0).

Copy this folder to config/custom_components/ on the dev instance and add
`quartermaster_probe:` to configuration.yaml. Never install it elsewhere.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

DOMAIN = "quartermaster_probe"

CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Nothing to set up: the llm.py platform does the work."""
    return True
