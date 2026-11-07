# Working memory

Shared state between development sessions and machines. Keep it short and factual, and free of secrets or personal data. Durable architecture decisions go to `TECHNICAL_NOTES.md`; this file tracks where the work stands. It will be replaced by a dedicated skill.

## Status

- Phase: 0 (checks and skeleton), in progress.
- Done: spec read; OctopusEnergyIT conventions reviewed; spec sanitized (Appendix A moved to `docs/ENVIRONMENT.local.md`, gitignored, local to the author's main machine only).
- In progress: 0.a official-docs review against spec sections 12/Phase 0.1 and 17; 0.b domain availability.
- Next: 0.c minimum HA version; 0.d skeleton of both repos; 0.e practical-checks kit.

## Decisions from the author (2026-11-07)

- Spec is committed without any personal or environment data.
- Alexa and iOS checks: the dev instance is exposed through a Cloudflare quick tunnel, created later on another machine.
- Ring camera checks (freshness and `camera.record`): run on the author's real instance, only when the author says so.
- Dashboard development: the integration's docker-compose mounts `../HomeAssistant-Quartermaster-Dashboard/dist` into the dev instance (single dev instance for both repos).
- Branching: work directly on `main` for now (overrides the dedicated-branch/PR rule until the author says otherwise). Still no version bump, no tags, no releases.
- Work may continue from two different machines: keep this file and `TECHNICAL_NOTES.md` up to date and pushed.

## Open questions for the author

- Domain `quartermaster` is already used by another custom integration (`quartermaster-app/ha-quartermaster`, released 2026-10-08, not yet in the HACS default catalog or in home-assistant/brands). Decide whether to keep the domain or rename.
- `gh` CLI is not authenticated on the main machine; needed before any GitHub operation.
