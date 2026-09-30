---
kind: backlog
flow: spend
symptom: "read takes since/until for spend_lane and spend_session, and list('spend_user') takes name, but only refusals say so; the argument descriptions still list the default types only"
decided: 2026-09-29, found by /dogfood, NA
recheck_when: src/opik_mcp/server/tools/feature_surface.py
---
Argument schemas stay unchanged by design; a feature could carry per-argument sentences the way it carries per-tool ones.
