"""Translated phrases spoken or sent by the integration itself.

They live under `common` in `translations/<language>.json` and are read with
`homeassistant.helpers.translation.async_get_translations`, a public helper
that the integration guides do not document (author decision, see
TECHNICAL_NOTES). Missing keys fall back to English.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.translation import async_get_translations

from custom_components.quartermaster.const import DOMAIN

_PREFIX = f"component.{DOMAIN}.common."


async def async_phrase(
    hass: HomeAssistant, language: str | None, key: str, **placeholders: str
) -> str:
    """Return a phrase in `language` (or the system language), filled in."""
    language = language or hass.config.language
    texts = await async_get_translations(hass, language, "common", [DOMAIN])
    text = texts.get(_PREFIX + key)
    if text is None and language != "en":
        english = await async_get_translations(hass, "en", "common", [DOMAIN])
        text = english.get(_PREFIX + key)
    if text is None:
        return key
    return text.format(**placeholders) if placeholders else text
