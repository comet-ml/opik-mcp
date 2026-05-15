# ADR 0006 — Customer-facing admin & dashboard surface deferred

**Status:** Accepted
**Date:** 2026-05-15
**Related:** design.md §1.13 (admin surface), §2.2.1 (DB schema), §2.2.5 (opik-frontend), §2.18 (workstreams)

## TL;DR

The customer-facing **MCP admin / dashboard surface** is **not part of the current launch**. That includes the workspace "MCP" settings tab in `opik-frontend`, the `mcp_workspace_settings` and `mcp_export_jobs` tables, the `/api/admin/mcp/*` endpoint family in `comet-backend`, the connected-clients-with-revoke-button UI, customer-facing usage/cost dashboards, the audit-export UI, the retention picker, and workstream **W6** as previously specified.

What stays:
- OAuth `/oauth/revoke` (RFC 7009) for token revocation.
- Audit log writes (`mcp_audit_log`) with a single global 12-month retention.
- Internal **SRE** observability (Prometheus, internal Grafana, alert rules, runbook).
- User-side scope controls via the OAuth consent screen.

## Context

The original §1.13 design described a substantial customer-facing administration surface: a workspace "MCP" tab with toggle switches (`mcp_enabled`, `ask_ollie_enabled`), per-scope chip disables, a connected-clients table with revoke buttons, a usage chart, an async audit export with polling, and an audit-retention picker (1–84 months, enterprise-gated >12). It was sized as workstream W6 in §2.18 — frontend + backend + workers — and pulled new tables, endpoints, background jobs, and a Freemarker `planTier` extension.

For the current launch, that scope is too large and too far from the critical path. The questions the surface answers (who's connected? what did Ollie do? turn off Ollie for this workspace) are **answerable, but they're not blockers for shipping the protocol** — the protocol works whether or not the workspace tab exists. SRE has its own observability for operating the service. Token revocation already has an RFC 7009 endpoint that hosts and `curl` can hit. Audit data is queryable by SQL until a UI ships.

## Decision

Drop the customer-facing admin/dashboard surface from launch scope. Specifically:

### What is **removed** from launch

| Item | What it was | Where it lived |
|---|---|---|
| `mcp_workspace_settings` table | Per-workspace flags: `mcp_enabled`, `ask_ollie_enabled`, `scope_disable` JSON, `mcp_audit_retention_months` | §2.2.1 DB schema, W2 migrations |
| `mcp_export_jobs` table | Async audit export job state | §2.2.1, §2.18 W6 background worker |
| `/api/admin/mcp/*` endpoint family | Connected clients list, revoke, usage report, settings GET/PATCH, export-job create/poll, retention PATCH | §2.2.1, §2.18 W2 |
| `opik-frontend` "MCP" workspace tab | Connected clients table, token revocation modal, usage chart, audit-export polling, two toggles, per-scope chip disables, retention picker | §2.2.5, §2.18 W6 |
| Customer-facing usage / cost dashboards | Per-workspace usage report UI, customer cost breakdown | §1.13, §2.11.2 |
| `planTier` extension to `RemoteAuthService.AuthResponse` | Tier-gating for retention picker >12 months | §2.2.1 auth contract |
| Workstream **W6** ("Admin + audit UI") | Frontend + backend + workers | §2.18 |
| Schemathesis property tests for the admin REST surface | Property-tests against admin OpenAPI | §2.16, §2.19 |

### What **stays** at launch

- **Token revocation:** standard OAuth `/oauth/revoke` (RFC 7009) — hosts can revoke their own tokens, users can revoke via the existing OAuth path.
- **Audit logging:** `mcp_audit_log` writes happen on every tool call, exactly as designed. Global retention is **12 months** (single value, not per-workspace).
- **Operational SRE observability:** Prometheus metrics (§2.11.4), internal Grafana dashboards (§2.11.2), alert rules (§2.11.3), runbook. These drive on-call and capacity planning; they are not exposed to customers.
- **User-side scope controls:** the OAuth consent screen still presents the full granular scope list; users can decline scopes per grant. This is the only customer-facing "MCP setting" at launch.
- **Per-workspace migration grace and trial bonus:** stored in Redis (`mcp:quota_modifier:{workspace_id}:*` keys with TTL), not in MySQL — since `mcp_workspace_settings` is removed (§1.9).

### Relocations forced by this decision

1. `migration_grace_until` and `trial_bonus_until` move from `mcp_workspace_settings` to Redis (`mcp:quota_modifier:{workspace_id}:migration_grace_until`, `mcp:quota_modifier:{workspace_id}:trial_bonus_until`). TTL is the natural expiry.
2. `mcp_audit_retention_months` does not exist — there is a single global 12-month retention constant in `ollie-mcp`'s pruning job.
3. The §2.18 workstream numbering shifts: old W6 is removed, old W7–W10 become W6–W9.
4. The Phase-1 doc (`phase-1.md`) and `architecture.md` no longer reference a "Phase 2 admin UI" — admin/dashboard is **not** scheduled as Phase 2; it's deferred without a date.
5. The §2.10.3 scope reasoning ("admin can disable runs") is rephrased as "a future admin surface can disable runs" — the scope split still buys that optionality, it just isn't exercised at launch.

## Behavior changes

| User-visible question | Today's answer | Launch answer (post-ADR 0006) |
|---|---|---|
| "How do I disconnect this MCP host from my workspace?" | Click revoke in the MCP tab. | Use your host's "disconnect connector" flow, or hit `/oauth/revoke` directly. |
| "How much have I spent on `ask_ollie` this month?" | Usage chart in the MCP tab. | Not exposed to customers at launch. Internally visible to SRE on the Grafana cost view. |
| "Can I see what Ollie did in my workspace?" | Audit export polling in the MCP tab. | Not exposed to customers at launch. Audit rows are queryable internally; ask support if you need an extract. |
| "Can I turn off `ask_ollie` for everyone in my workspace?" | Toggle in the MCP tab. | Not at launch. Per-user opt-out is via the OAuth consent screen (decline the `mcp:ask_ollie` scope). |
| "Can I keep audit logs longer than 12 months?" | Retention picker (1–84 mo). | No — single global 12-month retention at launch. |

## Pros / cons

**Pros**
- Removes a frontend + backend + worker workstream (W6) from the critical-to-GA scope. Frees Comet platform and opik-frontend capacity.
- Removes two DB tables and an `/api/admin/mcp/*` endpoint family from the W2 migration set — smaller backend surface to ship and test.
- Removes a Freemarker template extension (`planTier`) and a `RemoteAuthService.AuthResponse` field — fewer touch-points in `comet-backend`'s shared auth layer.
- Audit-retention picker had real complexity (per-workspace enforcement, enterprise-plan gating, retention re-prune cadence). Deleting it removes a non-trivial scope.
- The launch is honest about what it ships — "the protocol" — rather than carrying half-built dashboards.

**Cons**
- Customer cannot self-serve audit data. "Email support for an extract" is a worse experience than "click export."
- Customer cannot self-serve a per-workspace `ask_ollie` disable. Compliance-sensitive workspaces have to manage this via the OAuth consent screen, per user.
- No customer-facing cost visibility. Workspaces on overage caps don't get a UI to see why; they get a 429 with the cap in the error body and a runbook URL.
- Re-introducing the surface later is real work — the schema and endpoints aren't built. A future ADR will need to spec them fresh against whatever the world looks like at that point.

## Open questions

1. **How long is "deferred"?** We are explicitly **not** scheduling this as Phase 2. The next-priority MCP work after GA is operability, not customer admin UX. Revisit on customer pull, not on a calendar.
2. **Support-mediated audit extracts — process?** Until a self-serve export ships, customers requesting audit data go through support. Need a runbook entry in `docs/runbooks/ollie-mcp.md` describing the SQL query, the redaction policy, and the legal-review trigger.
3. **Single global 12-month retention — defensible for enterprise contracts?** Some enterprise deals reference 7-year audit retention. Mitigation: `mcp_audit_log` already writes to WORM S3 with 7-year Object Lock (§2.9 DR & backup row). The customer-visible retention is 12 months but the compliance copy is 7 years on WORM. Confirm with legal before signing the next contract that mentions audit retention.

## What this rules out

- Shipping a half-built admin tab that's only good enough to demo — every cut listed here either ships fully or doesn't ship.
- Mixing customer dashboards into the SRE Grafana — the internal dashboards are not customer-fit (label cardinality, raw error rows, infra noise) and should not be linked from customer-facing UI.
- Implicitly committing the next quarter's frontend capacity to "Phase 2 admin." This is deferred without a date; teams should plan capacity against actual product priorities.

## Notes

The structural argument: the MCP protocol does its job whether or not Comet has a settings tab for it. OAuth is the user-facing safety boundary, and `/oauth/revoke` plus the consent screen cover the two operations a user actually needs (kill a connector, decline a scope). Everything else in the original §1.13 — usage charts, audit export polling, per-workspace toggles, retention pickers — is **product surface around** the protocol, not the protocol itself.

Shipping the protocol first and the product surface later is also the empirically correct order for an integration like this: until we have GA traffic, we don't know which admin views are useful versus theatre. Building those views against guesses about how customers will use MCP is a worse bet than building them against a quarter of real traffic data.
