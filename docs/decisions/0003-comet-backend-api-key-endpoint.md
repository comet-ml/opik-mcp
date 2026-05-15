# ADR 0003 — `comet-backend` adds API-key-callable pod discovery

**Status:** PR open ([comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555))
**Date:** 2026-05-14

## Context

Today's `GET /opik/ollie/compute` in `comet-backend` is guarded by `@Auth DefaultJwtCookiePrincipal` only — it works for the React FE but rejects any API-only caller. Phase 1 of `opik-mcp` is a local process with no JWT cookie. Without API-key auth on this route, `ask_ollie` is non-functional in Phase 1.

## Options considered

### Option A — Make `@Auth` optional on the existing endpoint

Use `@Auth Optional<DefaultJwtCookiePrincipal>` and fall back to API-key auth when the cookie is missing. Single endpoint, both auth modes.

- ✅ Cleaner URL surface
- ❌ Depends on `dhatim/dropwizard-jwt-cookie-authentication` bundle supporting `Optional<Principal>` injection — not verified
- ❌ Larger blast radius; touches the route every browser request hits

### Option B — Add a sibling endpoint

Keep `GET /opik/ollie/compute` (cookie) unchanged. Add `GET /opik/ollie/compute-api-key` that accepts the standard `Authorization` / `Comet-Sdk-Api` header. Extract the pod-provisioning body into a shared private helper.

- ✅ Strictly additive — zero risk to browser flow
- ✅ Mirrors the existing `/auth` (API key) vs `/auth-session` (cookie) pattern in the same file
- ✅ Easy to remove later once Phase 2 OAuth lands
- ❌ Slightly more verbose URL surface

### Option C — ChainedAuthFilter at the bundle level

Modify `ReactWebappServerApplication` to register a chained authenticator (cookie + API key) and apply it to `/ollie/compute`.

- ✅ Cleanest in principle
- ❌ Bundle-level change touches *every* route in `comet-rest`
- ❌ Tiny-fix territory blown wide open

## Decision

**Option B — sibling endpoint.**

The patch is 12 lines:
- `GET /opik/ollie/compute` stays as-is (cookie auth, browser callers)
- `GET /opik/ollie/compute-api-key` is new (API-key auth via `RestHelpers.getUserNameFromRestApiAuth`)
- Both delegate to `provisionOlliePod(userName, workspaceName)` (extracted private helper)

PR: [comet-ml/comet-backend#5555](https://github.com/comet-ml/comet-backend/pull/5555)

## What this enables

Phase 1 of `opik-mcp` can ship with `ask_ollie` working end-to-end against the cloud Comet stack:

```
opik-mcp → GET /opik/ollie/compute-api-key (Authorization: $OPIK_API_KEY)
         ← {computeURL, enabled} + Set-Cookie: PPAUTH=<browserAuth>
opik-mcp → poll computeURL/health/ready with PPAUTH cookie
opik-mcp → POST computeURL/sessions with PPAUTH cookie → SSE stream
```

No other backend changes needed for Phase 1.

## What this does NOT do

- No OAuth — that's Phase 2
- No JWT verifier on the pod — pod stays on `PPAUTH` cookie, unchanged
- No `/oauth/mint-user-api-key` — local install uses `OPIK_API_KEY` directly
- No multi-tenant pods — pod is still keyed by user × org (`CodePanelComputeService:197`)

## Removal plan

Once Phase 2 OAuth ships and verified hosts are registered via CIMD, this endpoint becomes the API-key fallback path for:
- Self-hosted installs (no OAuth AS to stand up)
- CI / scripted callers that prefer API keys

On cloud, OAuth becomes primary. The endpoint stays as a documented fallback.
