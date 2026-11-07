# Working memory

Shared state between development sessions and machines. Keep it short and factual, and free of secrets or personal data. Durable architecture decisions go to `TECHNICAL_NOTES.md`; this file tracks where the work stands. It will be replaced by a dedicated skill.

## Status

- Phase: 0 (checks and skeleton), in progress.
- Done: spec read; OctopusEnergyIT conventions reviewed; spec sanitized (Appendix A moved to `docs/ENVIRONMENT.local.md`, gitignored, local to the author's main machine only).
- Done: 0.a docs review and 0.b domain check (results in TECHNICAL_NOTES.md, Phase 0 section).
- Next: author decisions on TECHNICAL_NOTES rows marked R (min HA 2026.11.0, button notifications, legacy HACS section); then 0.d skeleton of both repos; 0.e practical-checks kit.

## Decisions from the author (2026-11-07)

- Spec is committed without any personal or environment data.
- Alexa and iOS checks: the dev instance is exposed through a Cloudflare quick tunnel, created later on another machine.
- Ring camera checks (freshness and `camera.record`): run on the author's real instance, only when the author says so.
- Dashboard development: the integration's docker-compose mounts `../HomeAssistant-Quartermaster-Dashboard/dist` into the dev instance (single dev instance for both repos).
- Branching: work directly on `main` for now (overrides the dedicated-branch/PR rule until the author says otherwise). Still no version bump, no tags, no releases.
- Domain stays `quartermaster` (confirmed 2026-11-07) despite `quartermaster-app/ha-quartermaster` using the same domain (released 2026-10-08, not in hacs/default or brands at that date). Document the conflict in the README and TECHNICAL_NOTES.
- Pushing to `main` is allowed.
- Release tags use the `v` prefix (`v1.0.0`), as in OctopusEnergyIT.
- Work may continue from two different machines: keep this file and `TECHNICAL_NOTES.md` up to date and pushed.

## Open questions for the author

- None.
