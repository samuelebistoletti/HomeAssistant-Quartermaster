"""Quartermaster: household inventory and shopping list agent for Home Assistant."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
import sqlite3

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .channels.telegram import TelegramChannel, parse_chat_ids
from .channels.websocket import async_setup_websocket
from .const import (
    CONF_AGENT_ID,
    CONF_AI_TASK_IMAGE,
    CONF_ASK_SESSION_SECONDS,
    CONF_DAILY_ANALYSIS_LIMIT,
    CONF_EXTERNAL_TODO,
    CONF_STRUCTURE_MODE,
    CONF_TELEGRAM_BOT,
    CONF_TELEGRAM_CHATS,
    CONF_TELEGRAM_EVENT,
    CONF_TELEGRAM_GROUPS,
    DATABASE_FILE,
    DEFAULT_ASK_SESSION_SECONDS,
    DEFAULT_DAILY_ANALYSIS_LIMIT,
    DEFAULT_STRUCTURE_MODE,
    DOMAIN,
)
from .coordinator import QuartermasterCoordinator
from .core.confirmations import ProposalService, ToolConfirmations
from .core.initial import async_apply_initial_setup
from .core.inventory import InventoryService
from .core.shopping import ShoppingService
from .core.voice import VoiceSessions
from .core.zones import ZoneManager
from .notify import NotificationRouter
from .services import async_setup_services
from .storage.sqlite import SchemaTooNewError, SQLiteBackend
from .vision.analyze import VisionService
from .vision.schema import StructureMode
from .vision.show import ShowManager

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class QuartermasterRuntimeData:
    """Runtime objects shared by every platform of the config entry."""

    inventory: InventoryService
    shopping: ShoppingService
    coordinator: QuartermasterCoordinator
    voice: VoiceSessions
    confirmations: ToolConfirmations
    vision: VisionService
    proposals: ProposalService
    zones: ZoneManager = field(init=False)
    show: ShowManager = field(init=False)
    telegram: TelegramChannel | None = None
    notifier: NotificationRouter = field(init=False)


type QuartermasterConfigEntry = ConfigEntry[QuartermasterRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration actions."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Set up Quartermaster from a config entry."""
    backend = SQLiteBackend(hass, Path(hass.config.path(DOMAIN, DATABASE_FILE)))
    try:
        await backend.async_open()
    except SchemaTooNewError as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="database_too_new",
            translation_placeholders={"found": str(err.found)},
        ) from err
    except (OSError, sqlite3.Error) as err:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="database_error",
            translation_placeholders={"error": str(err)},
        ) from err
    entry.async_on_unload(backend.async_close)
    inventory = InventoryService(backend)
    shopping = ShoppingService(hass, backend, inventory)
    shopping.external_entity_id = entry.options.get(CONF_EXTERNAL_TODO)
    await async_apply_initial_setup(hass, entry, inventory, shopping)
    coordinator = QuartermasterCoordinator(hass, entry, inventory, shopping)
    proposals = ProposalService(inventory)
    vision = VisionService(hass, inventory)
    vision.ai_task_entity = entry.options.get(CONF_AI_TASK_IMAGE)
    vision.structure_mode = StructureMode(
        entry.options.get(CONF_STRUCTURE_MODE, DEFAULT_STRUCTURE_MODE)
    )
    vision.daily_limit = int(
        entry.options.get(CONF_DAILY_ANALYSIS_LIMIT, DEFAULT_DAILY_ANALYSIS_LIMIT)
    )
    coordinator.proposals = proposals
    coordinator.vision = vision
    await coordinator.async_config_entry_first_refresh()
    entry.async_on_unload(
        inventory.async_add_listener(coordinator.async_inventory_changed)
    )
    voice = VoiceSessions(
        hass,
        agent_id=entry.options.get(CONF_AGENT_ID),
        lifetime=timedelta(
            seconds=entry.options.get(
                CONF_ASK_SESSION_SECONDS, DEFAULT_ASK_SESSION_SECONDS
            )
        ),
    )
    entry.async_on_unload(voice.async_shutdown)
    entry.runtime_data = QuartermasterRuntimeData(
        inventory=inventory,
        shopping=shopping,
        coordinator=coordinator,
        voice=voice,
        confirmations=ToolConfirmations(),
        vision=vision,
        proposals=proposals,
    )
    zones = ZoneManager(hass, entry.runtime_data)
    entry.runtime_data.zones = zones
    coordinator.zones = zones
    zones.async_start()
    entry.async_on_unload(zones.async_stop)
    show = ShowManager(hass, entry.runtime_data)
    entry.runtime_data.show = show
    coordinator.show = show
    show.async_start(list(coordinator.data.zones.values()))
    entry.async_on_unload(show.async_shutdown)
    if event_entity := entry.options.get(CONF_TELEGRAM_EVENT):
        telegram = TelegramChannel(
            hass,
            entry.runtime_data,
            event_entity=event_entity,
            bot_entry_id=entry.options.get(CONF_TELEGRAM_BOT),
            chats=parse_chat_ids(entry.options.get(CONF_TELEGRAM_CHATS, [])),
            allow_groups=bool(entry.options.get(CONF_TELEGRAM_GROUPS, False)),
        )
        entry.runtime_data.telegram = telegram
        telegram.async_start()
        entry.async_on_unload(telegram.async_stop)
    notifier = NotificationRouter(hass, entry.runtime_data)
    entry.runtime_data.notifier = notifier
    coordinator.notifier = notifier
    notifier.async_start()
    entry.async_on_unload(notifier.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)


async def async_unload_entry(
    hass: HomeAssistant, entry: QuartermasterConfigEntry
) -> None:
    """Unload a config entry."""
    await hass.config_entries.async_unload_platforms_or_raise(entry, PLATFORMS)
