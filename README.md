<p align="center">
  <img src="custom_components/quartermaster/brand/logo.png" alt="Quartermaster" width="460">
</p>

# Quartermaster

An AI agent for Home Assistant that keeps track of your pantry, fridge, freezer and shelves, and fills in the shopping list for you. You talk to it through Assist, Alexa, Telegram, the Home Assistant app and automations; it reads photos and camera snapshots through the AI provider you already use in Home Assistant.

> [!WARNING]
> Quartermaster is under development and not usable yet. There is no release; the first one will be 1.0.0. The design is in [docs/SPEC.md](docs/SPEC.md).

## Requirements

- Home Assistant 2026.11.0 or later.
- An AI Task entity and a conversation agent from any provider configured in Home Assistant (cloud or local).

## Installation

Once 1.0.0 is released, Quartermaster will be installable from the Marketplace (**Settings** → **Marketplace**). Until it is in the default catalog, add this repository as a custom repository there.

The dashboard strategy and cards live in a separate repository, [HomeAssistant-Quartermaster-Dashboard](https://github.com/samuelebistoletti/HomeAssistant-Quartermaster-Dashboard), installed from the Marketplace as a dashboard.

### Domain conflict

Another custom integration, [quartermaster-app/ha-quartermaster](https://github.com/quartermaster-app/ha-quartermaster), uses the same `quartermaster` domain. Home Assistant can load only one integration per domain, so the two cannot be installed together.

## Removal

1. **Settings** → **Devices & services** → **Quartermaster** → **⋮** → **Delete**.
2. Remove the integration from the Marketplace.

## Compatibility

| Quartermaster | Quartermaster Dashboard | Home Assistant |
| --- | --- | --- |
| unreleased | unreleased | 2026.11.0+ |

## License

[MIT](LICENSE)
