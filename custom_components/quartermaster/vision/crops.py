"""Crops kept for the dashboard: the last one of each zone and those of proposals.

Only crops of zone photos and zone camera checks are kept (spec §6.5), in
`<config>/quartermaster/crops`, outside the media folder so they are not
browsable. camera_show and passage frames are never kept (spec §5.1). A
proposal's crop is deleted when the proposal is decided or expires.
"""

from __future__ import annotations

from pathlib import Path
import re

from homeassistant.core import HomeAssistant

from custom_components.quartermaster.const import DOMAIN

CROPS_DIR = "crops"
_SAFE = re.compile(r"^[a-z0-9_]+$")


class CropStore:
    """Read and write kept crops in the executor."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Keep crops under the integration's folder."""
        self._hass = hass
        self._root = Path(hass.config.path(DOMAIN, CROPS_DIR))

    def _path(self, kind: str, key: str | int) -> Path:
        name = f"{kind}_{key}"
        if not _SAFE.match(name):
            raise ValueError(name)
        return self._root / f"{name}.jpg"

    async def async_save(self, kind: str, key: str | int, jpeg: bytes) -> None:
        """Store a crop, replacing the previous one."""
        await self._hass.async_add_executor_job(_write, self._path(kind, key), jpeg)

    async def async_read(self, kind: str, key: str | int) -> bytes | None:
        """Return a crop, or None."""
        try:
            path = self._path(kind, key)
        except ValueError:
            return None
        return await self._hass.async_add_executor_job(_read, path)

    async def async_delete(self, kind: str, key: str | int) -> None:
        """Delete a crop if it exists."""
        await self._hass.async_add_executor_job(_delete, self._path(kind, key))

    async def async_keys(self, kind: str) -> set[str]:
        """Return the keys of the stored crops of a kind."""
        names = await self._hass.async_add_executor_job(_list, self._root)
        prefix = f"{kind}_"
        return {name[len(prefix) :] for name in names if name.startswith(prefix)}

    async def async_has(self, kind: str, key: str | int) -> bool:
        """Return whether a crop exists."""
        return str(key) in await self.async_keys(kind)

    async def async_prune(self, kind: str, keep: set[str]) -> None:
        """Delete the crops of a kind whose key is not in `keep`."""
        for key in await self.async_keys(kind) - keep:
            await self.async_delete(kind, key)


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _delete(path: Path) -> None:
    path.unlink(missing_ok=True)


def _list(root: Path) -> list[str]:
    if not root.is_dir():
        return []
    return [path.stem for path in root.glob("*.jpg")]
