"""Repair issues for configured AI entities that are gone.

The AI Task entities and the conversation agent chosen in the options are
checked at setup and whenever the entity registry or their state changes. An
entity counts as present when the entity registry knows it (loaded or not, so
a slow start of its integration raises nothing) or it has a state.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_AGENT_ID, CONF_AI_TASK_IMAGE, CONF_AI_TASK_TEXT, DOMAIN

WATCHED_OPTIONS: tuple[str, ...] = (
    CONF_AI_TASK_IMAGE,
    CONF_AI_TASK_TEXT,
    CONF_AGENT_ID,
)


def _issue_id(option: str) -> str:
    return f"missing_entity_{option}"


class MissingEntityWatcher:
    """Raise and clear a repair issue per configured entity that is gone."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Read the configured entities; nothing runs until `async_start`."""
        self._hass = hass
        # Conversation agents may also be given by a config entry id (agents
        # that are not entities); only entity ids are checked.
        self._entities = {
            option: str(entity_id)
            for option in WATCHED_OPTIONS
            if (entity_id := entry.options.get(option)) and "." in str(entity_id)
        }
        self._unsubs: list[CALLBACK_TYPE] = []

    @callback
    def async_start(self) -> None:
        """Check now and after every change of the entities."""
        if not self._entities:
            return
        watched = set(self._entities.values())

        @callback
        def registry_changed(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
            data = event.data
            ids = {data["entity_id"]}
            if data["action"] == "update" and "old_entity_id" in data:
                ids.add(data["old_entity_id"])
            if ids & watched:
                self.async_check()

        self._unsubs.append(
            self._hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, registry_changed
            )
        )

        @callback
        def state_changed(_event: Event[EventStateChangedData]) -> None:
            self.async_check()

        self._unsubs.append(
            async_track_state_change_event(self._hass, list(watched), state_changed)
        )
        self.async_check()

    @callback
    def async_stop(self) -> None:
        """Stop listening and drop the issues: a reload checks again."""
        while self._unsubs:
            self._unsubs.pop()()
        for option in self._entities:
            ir.async_delete_issue(self._hass, DOMAIN, _issue_id(option))

    @callback
    def async_check(self) -> None:
        """Raise or clear the issue of each configured entity."""
        registry = er.async_get(self._hass)
        for option, entity_id in self._entities.items():
            if (
                registry.async_get(entity_id) is not None
                or self._hass.states.get(entity_id) is not None
            ):
                ir.async_delete_issue(self._hass, DOMAIN, _issue_id(option))
                continue
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                _issue_id(option),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=_issue_id(option),
                translation_placeholders={"entity": entity_id},
            )
