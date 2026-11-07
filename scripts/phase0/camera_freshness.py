#!/usr/bin/env python3
r"""Phase 0: how fresh is a camera snapshot read 1, 3 and 5 s after motion.

Read-only: it subscribes to a trigger entity over the websocket API and reads
the camera through /api/camera_proxy. Nothing is written to Home Assistant and
no image is saved: only sizes, timings, brightness, sharpness and perceptual
hashes go to a CSV outside the repository.

    HA_URL=https://... HA_TOKEN=... uv run --with aiohttp --with pillow \
        scripts/phase0/camera_freshness.py --camera camera.x --trigger event.y \
        [--delays 0,1,3,5] [--events 5] [--battery sensor.z] [--out DIR]

The token is read from the environment and never stored.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import UTC, datetime
import io
import os
from pathlib import Path
import time

import aiohttp
from PIL import Image, ImageFilter, ImageStat


def dhash(image: Image.Image, size: int = 8) -> int:
    """Difference hash: 64 bits comparing adjacent pixels of a tiny grayscale."""
    small = image.convert("L").resize((size + 1, size))
    px = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = px[row * (size + 1) + col]
            right = px[row * (size + 1) + col + 1]
            bits = (bits << 1) | (left > right)
    return bits


def describe(data: bytes) -> dict[str, object]:
    """Measure a frame without keeping it."""
    image = Image.open(io.BytesIO(data))
    gray = image.convert("L")
    edges = gray.filter(ImageFilter.FIND_EDGES)
    return {
        "width": image.width,
        "height": image.height,
        "bytes": len(data),
        "brightness": round(ImageStat.Stat(gray).mean[0], 1),
        "sharpness": round(ImageStat.Stat(edges).var[0], 1),
        "dhash": dhash(image),
    }


class Probe:
    """Websocket subscription plus camera reads."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.url = os.environ["HA_URL"].rstrip("/")
        self.headers = {"Authorization": f"Bearer {os.environ['HA_TOKEN']}"}
        self.rows: list[dict[str, object]] = []
        self.baseline: int | None = None

    async def read_frame(self, session: aiohttp.ClientSession) -> dict[str, object]:
        start = time.monotonic()
        async with session.get(
            f"{self.url}/api/camera_proxy/{self.args.camera}",
            headers=self.headers,
            timeout=aiohttp.ClientTimeout(total=20),
        ) as resp:
            data = await resp.read()
            status = resp.status
        info: dict[str, object] = {
            "status": status,
            "fetch_s": round(time.monotonic() - start, 2),
        }
        if status == 200:
            info.update(describe(data))
        return info

    async def read_state(self, session: aiohttp.ClientSession, entity: str) -> str:
        async with session.get(
            f"{self.url}/api/states/{entity}", headers=self.headers
        ) as resp:
            return str((await resp.json()).get("state"))

    async def on_trigger(self, session: aiohttp.ClientSession, n: int) -> None:
        t0 = time.monotonic()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        print(f"event {n} at {now}")
        hashes: dict[float, int] = {}
        for delay in self.args.delays:
            await asyncio.sleep(max(0.0, delay - (time.monotonic() - t0)))
            frame = await self.read_frame(session)
            h = frame.get("dhash")
            row = {"event": n, "event_time": now, "delay_s": delay, **frame}
            if isinstance(h, int):
                hashes[delay] = h
                if self.baseline is not None:
                    row["bits_vs_baseline"] = (h ^ self.baseline).bit_count()
                first = next(iter(hashes.values()))
                row["bits_vs_first"] = (h ^ first).bit_count()
            self.rows.append(row)
            print(f"  +{delay}s {row}")

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            battery_start = (
                await self.read_state(session, self.args.battery)
                if self.args.battery
                else ""
            )
            base = await self.read_frame(session)
            self.baseline = base.get("dhash")  # type: ignore[assignment]
            print(f"baseline {base}")
            async with session.ws_connect(f"{self.url}/api/websocket") as ws:
                await ws.receive_json()
                await ws.send_json(
                    {"type": "auth", "access_token": os.environ["HA_TOKEN"]}
                )
                if (await ws.receive_json()).get("type") != "auth_ok":
                    raise SystemExit("websocket authentication failed")
                await ws.send_json(
                    {
                        "id": 1,
                        "type": "subscribe_trigger",
                        "trigger": {
                            "trigger": "state",
                            "entity_id": self.args.trigger,
                        },
                    }
                )
                print(f"waiting for {self.args.events} events on {self.args.trigger}")
                seen = 0
                while seen < self.args.events:
                    msg = await ws.receive_json()
                    if msg.get("type") == "event":
                        seen += 1
                        await self.on_trigger(session, seen)
            battery_end = (
                await self.read_state(session, self.args.battery)
                if self.args.battery
                else ""
            )
        self.write(battery_start, battery_end)

    def write(self, battery_start: str, battery_end: str) -> None:
        out = Path(self.args.out).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"camera_freshness_{datetime.now(UTC):%Y%m%dT%H%M%S}.csv"
        fields = sorted({k for row in self.rows for k in row})
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.rows)
        print(f"written {path}")
        if self.args.battery:
            print(f"battery {battery_start} -> {battery_end}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--camera", required=True)
    parser.add_argument("--trigger", required=True)
    parser.add_argument(
        "--delays", default="0,1,3,5", type=lambda s: [float(x) for x in s.split(",")]
    )
    parser.add_argument("--events", default=5, type=int)
    parser.add_argument("--battery", default="")
    parser.add_argument("--out", default="~/quartermaster-phase0")
    asyncio.run(Probe(parser.parse_args()).run())


if __name__ == "__main__":
    main()
