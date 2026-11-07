# Working memory

Shared state between development sessions and machines. Keep it short and factual, and free of secrets or personal data. Durable architecture decisions go to `TECHNICAL_NOTES.md`; this file tracks where the work stands. It will be replaced by a dedicated skill.

## Status

- Phase: 0 (checks and skeleton), in progress.
- Done: spec read; OctopusEnergyIT conventions reviewed; spec sanitized (Appendix A moved to `docs/ENVIRONMENT.local.md`, gitignored, local to the author's main machine only).
- Done: 0.a docs review and 0.b domain check (results in TECHNICAL_NOTES.md, Phase 0 section).
- Done: 0.c (2026.11.0).
- Done: 0.d skeleton of both repos (local: tests, ruff, mypy, hassfest green; config flow verified on the dev instance).
- Done: CI green on GitHub for both repos (no tags or releases).
- Done: 0.e practical-checks kit (docs/phase0/CHECKLIST.md, examples/phase0/, scripts/phase0/); Alexa HA-side behaviour verified by simulation.
- Next: run the practical checks with the author (Telegram, Alexa and iOS through a quick tunnel on the second machine; camera on the real instance; Marketplace and Cloudflare on a network without TLS inspection).

## Decisions from the author (2026-11-07)

- Spec is committed without any personal or environment data.
- Alexa and iOS checks: the dev instance is exposed through a Cloudflare quick tunnel, created later on another machine.
- Ring camera checks (freshness and `camera.record`): run on the author's real instance, only when the author says so.
- Dashboard development: the integration's docker-compose mounts `../HomeAssistant-Quartermaster-Dashboard/dist` into the dev instance (single dev instance for both repos).
- Branching: work directly on `main` for now (overrides the dedicated-branch/PR rule until the author says otherwise). Still no version bump, no tags, no releases.
- Domain stays `quartermaster` (confirmed 2026-11-07) despite `quartermaster-app/ha-quartermaster` using the same domain (released 2026-10-08, not in hacs/default or brands at that date). Document the conflict in the README and TECHNICAL_NOTES.
- Pushing to `main` is allowed.
- Minimum HA 2026.11.0 (Python >= 3.14.2); no legacy-HACS install section.
- Action buttons only on Telegram and in the dashboard; other notify entities get plain text.
- Everything user-facing is multilingual, Alexa included (models and intent_script per language).
- Cloudflare AI Gateway community integration: wait for the upstream fix (open PRs michaeldwan/ha-cloudflare-ai-gateway#4 and #5 add voluptuous-openapi as a requirement; whether it handles probatio schemas must be re-checked after the merge). Cloudflare checks wait for it.
- Release tags use the `v` prefix (`v1.0.0`), as in OctopusEnergyIT.
- Work may continue from two different machines: keep this file and `TECHNICAL_NOTES.md` up to date and pushed.

## Open questions for the author

- Alexa launch language: HA drops the request locale; one fixed launch language per skill, or one skill per language?
