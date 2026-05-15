# ADR 0002 — Separate repo, not inside `ollie-assist`

**Status:** Accepted
**Date:** 2026-05-12

## Context

`ollie-assist` is the existing per-user pod that hosts the Ollie investigative agent. A reasonable instinct is "just add the MCP server inside `ollie-assist`."

## Decision

`opik-mcp` lives in its own repo (`comet-ml/opik-mcp`) with its own Docker image, deployable independently.

## Rationale

Three hard blockers make co-locating the MCP server inside `ollie-assist` untenable.

### 1. Per-user pods have no stable external URL

`ollie-assist` runs **one pod per workspace**, scaled from zero by an external orchestrator (helm install/uninstall on first request, idle TTL). The pod URL is dynamic — discovered via `comet-backend GET /api/opik/ollie/compute` → `codepanels` orchestrator.

External MCP hosts (Claude Code, Cursor, claude.ai) register **one URL** in their config and cannot do per-call workspace→pod discovery. So the MCP endpoint has to live somewhere with a stable, predictable URL.

### 2. Cold start is up to two minutes

MCP hosts time out on `tools/call` after ~30 seconds. A cold pod takes up to 2 minutes (helm install + image pull + warmup).

The MCP server needs to:
- Return `CreateTaskResult` within 2 seconds — **before the pod is even awake**
- Narrate warmup progress via `notifications/tasks/updated`
- Be **always warm** (≥2 replicas, no scale-to-zero)

A per-user pod cannot be always-warm by definition — that defeats the cost model.

### 3. The per-user pod has no OAuth and no JWT verifier

Today `ollie-assist` trusts a `BROWSER_AUTH` cookie value injected at pod creation that matches the user's Comet session cookie. Putting OAuth there means every per-user pod re-implements a JWT verifier with key rotation, JWKS caching, Redis pub-sub invalidation for emergency rotation.

That's the wrong tier for that work. The MCP server sits in front, validates JWTs once, and forwards a short-lived service-account JWT to the pod (Phase 2 only — Phase 1 keeps cookie auth on the pod side).

## Architecture consequence

```
                     Stable URL, always-warm, multi-replica
                     OAuth resource server, Tasks engine
                                    │
External MCP host ────MCP HTTP──────▶ opik-mcp (NEW repo)
                                    │
                                    ▼
        ┌───────────────────────────┼──────────────────────────┐
        │                           │                          │
        ▼                           ▼                          ▼
 comet-backend             ollie-assist pod              opik-backend
 (OAuth AS, pod discovery) (per-user, scaled from zero)  (REST upstream)
```

## What this rules out

- A single repo for "all MCP work"
- Shipping the MCP endpoint as a route inside `ollie-assist`
- Scale-to-zero for the MCP endpoint
- Cookie-only auth for external hosts

## What this implies

- A dedicated `comet-ml/opik-mcp` GitHub repo (this one)
- Its own Docker image (`ghcr.io/comet-ml/opik-mcp:<date>-<patch>`)
- Its own deployment lifecycle (Phase 2: always-warm K8s Deployment; Phase 1: locally installed Python process)
- Its own release cadence, independent of `ollie-assist`
- Shared types via PyPI dependency (`ollie-assist-types` package extracted from `ollie-assist`)

## Notes

`opik-mcp` (TypeScript, the existing scripted-CI path) is a separate concern and lives in its own NPM package. This ADR is about the new Python server only.
