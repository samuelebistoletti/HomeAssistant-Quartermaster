"""The Alexa example files match scripts/alexa-models and each other."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from homeassistant.core import HomeAssistant
from homeassistant.helpers.template import Template
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples" / "alexa"


def test_generated_files_are_current() -> None:
    """Run the generator in check mode: the committed files are up to date."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "alexa-models"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


def test_every_intent_has_a_script() -> None:
    """Each intent of every locale is handled, and puts its carrier word back."""
    scripts = yaml.safe_load((EXAMPLES / "quartermaster_alexa.yaml").read_text())[
        "intent_script"
    ]
    for path in EXAMPLES.glob("interaction_model.*.json"):
        language = json.loads(path.read_text())["interactionModel"]["languageModel"]
        for intent in language["intents"]:
            if intent["name"] == "AMAZON.NavigateHomeIntent":
                continue
            assert intent["name"] in scripts, (path.name, intent["name"])
            sample = intent["samples"][0] if intent["samples"] else ""
            if sample.endswith(" {query}"):
                text = scripts[intent["name"]]["action"][0]["data"]["text"]
                assert text.startswith(sample.removesuffix("{query}")), intent["name"]


async def test_session_key_per_device(hass: HomeAssistant) -> None:
    """Every ask keys the session by the Echo device the Lambda passes on."""
    scripts = yaml.safe_load((EXAMPLES / "quartermaster_alexa.yaml").read_text())[
        "intent_script"
    ]
    keys = {
        script["action"][0]["data"]["session_key"]
        for script in scripts.values()
        if "action" in script
    }
    assert len(keys) == 1
    key = Template(keys.pop(), hass)
    assert key.async_render({"qm_device": "amzn1.ask.device.A"}) == (
        "alexa-amzn1.ask.device.A"
    )
    # An unchanged Lambda: empty, so the user's session is used as before.
    assert key.async_render({"query": "latte"}) == ""
