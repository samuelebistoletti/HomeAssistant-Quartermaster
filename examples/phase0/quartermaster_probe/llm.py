"""Fake stock lookup tool used to check that an agent calls llm.py tools."""

from __future__ import annotations

import logging

from homeassistant.components.llm import LLMTools
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import llm
import probatio

_LOGGER = logging.getLogger(__name__)

# Fixed fake inventory: the expected answers are known in advance.
FAKE_STOCK = {
    "milk": {"quantity": 2, "unit": "bottle", "zone": "fridge"},
    "latte": {"quantity": 2, "unit": "bottiglia", "zone": "frigorifero"},
    "coffee": {"quantity": 3, "unit": "pack", "zone": "pantry"},
    "caffè": {"quantity": 3, "unit": "pacco", "zone": "dispensa"},
}


class GetStockTool(llm.Tool):
    """Return the fake quantity of a product."""

    name = "quartermaster_probe__get_stock"
    description = "Return how much of a product is in the house and in which zone."
    parameters = probatio.Schema({probatio.Required("product"): str})
    annotations = llm.ToolAnnotations(
        read_only=True, destructive=False, idempotent=True, open_world=False
    )
    integration = "quartermaster_probe"

    async def async_call(
        self,
        hass: HomeAssistant,
        tool_input: llm.ToolInput,
        llm_context: llm.LLMContext,
    ) -> llm.ToolResult:
        """Look the product up in the fake inventory."""
        product = str(tool_input.tool_args["product"]).strip().lower()
        _LOGGER.warning(
            "probe tool called: product=%s language=%s platform=%s",
            product,
            llm_context.language,
            llm_context.platform,
        )
        if (item := FAKE_STOCK.get(product)) is None:
            return llm.ToolResult(data={"product": product, "found": False})
        return llm.ToolResult(data={"product": product, "found": True, **item})


@callback
def async_get_tools(
    hass: HomeAssistant, llm_context: llm.LLMContext, api_id: str
) -> LLMTools | None:
    """Offer the probe tool to the Assist API only."""
    if api_id != llm.LLM_API_ASSIST:
        return None
    return LLMTools(
        tools=[GetStockTool()],
        prompt="Use quartermaster_probe__get_stock to answer questions about household stock.",
    )
