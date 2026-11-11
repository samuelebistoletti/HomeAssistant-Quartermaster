"""Translated phrases spoken or sent by the integration itself.

They live under `common` in `translations/<language>.json` and are read with
`homeassistant.helpers.translation.async_get_translations`, a public helper
that the integration guides do not document (author decision, see
TECHNICAL_NOTES). Missing keys fall back to English.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.translation import async_get_translations

from custom_components.quartermaster.const import CONF_LANGUAGE, DOMAIN

_PREFIX = f"component.{DOMAIN}.common."


def configured_language(hass: HomeAssistant) -> str:
    """Return the language chosen in the options, or the system language."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if language := entry.options.get(CONF_LANGUAGE):
            return str(language)
    return hass.config.language


async def async_phrase(
    hass: HomeAssistant, language: str | None, key: str, **placeholders: str
) -> str:
    """Return a phrase in `language`, filled in.

    Without a language, the one chosen in the options is used, then the
    system language.
    """
    language = language or configured_language(hass)
    texts = await async_get_translations(hass, language, "common", [DOMAIN])
    text = texts.get(_PREFIX + key)
    if text is None and language != "en":
        english = await async_get_translations(hass, "en", "common", [DOMAIN])
        text = english.get(_PREFIX + key)
    if text is None:
        return key
    return text.format(**placeholders) if placeholders else text


async def async_error_text(
    hass: HomeAssistant, language: str | None, err: HomeAssistantError
) -> str:
    """Return the message of a translated error in `language`.

    `str(err)` is always English; a chat is answered in its own language.
    """
    if err.translation_domain != DOMAIN or not err.translation_key:
        return str(err)
    language = language or configured_language(hass)
    key = f"component.{DOMAIN}.exceptions.{err.translation_key}.message"
    for code in dict.fromkeys((language, "en")):
        texts = await async_get_translations(hass, code, "exceptions", [DOMAIN])
        if (text := texts.get(key)) is not None:
            try:
                return text.format(**(err.translation_placeholders or {}))
            except KeyError:
                break
    return str(err)
