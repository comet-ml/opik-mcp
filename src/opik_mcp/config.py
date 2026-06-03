from functools import lru_cache
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from opik_mcp.error_kinds import ErrorKind

# Workspace name used when none is configured — mirrors the Opik Python SDK
# (`OPIK_WORKSPACE_DEFAULT_NAME = "default"`). Lets local/OSS users run without
# setting a workspace at all; cloud users with named workspaces still set one.
DEFAULT_WORKSPACE = "default"


class MissingConfigError(RuntimeError):
    """Raised when an ask_ollie call is attempted without required env vars.

    Deterministic and classifiable (the server is missing api_key/workspace),
    so it buckets as ``validation`` rather than ``unknown`` — it's a malformed
    setup the user can fix, not an unexpected fault.
    """

    error_kind: ClassVar[ErrorKind] = "validation"
    http_status: ClassVar[int | None] = 400


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, populate_by_name=True)

    opik_api_key: str | None = None
    # Workspace name. OPIK_WORKSPACE is the primary env var (matches the Opik
    # SDK and opik-mcp's OPIK_ convention); COMET_WORKSPACE is a deprecated
    # backward-compat fallback. Kept ``str | None`` (not defaulted) so the
    # analytics `has_workspace` flag still reflects whether the user set one;
    # the "default" fallback is applied at the resolve sites, not here.
    comet_workspace: str | None = Field(
        default=None,
        validation_alias=AliasChoices("opik_workspace", "comet_workspace"),
    )
    # Optional workspace UUID. Stamped into analytics events as
    # `workspace_id` when set so BI can JOIN on a stable identifier instead
    # of the workspace name (which is mutable). Unset means the field is
    # omitted from events; a future iteration can resolve it from the
    # backend at startup.
    comet_workspace_id: str | None = None
    comet_url_override: str = "https://www.comet.com"

    # Optional override for the Opik REST base. If unset, derived from
    # comet_url_override + "/opik/api". Set this for non-standard deployments
    # where Opik lives on a different host or path than the Comet UI.
    opik_url: str | None = None

    # Default project hint surfaced to the LLM via the instructions blob.
    # Tools remain stateless — the LLM is responsible for passing
    # `project_name` on each call. Unset = no hint, LLM must discover or ask.
    # We use names (not UUIDs) to match the Opik Python/TS SDKs, which expose
    # only `project_name` on every write path. The backend's write DTOs treat
    # `project_id` as READ_ONLY on traces/spans.
    opik_default_project_name: str | None = None

    opik_mcp_pod_ready_timeout_s: int = 120
    opik_mcp_pod_ready_interval_s: int = 2

    # Cadence for the watchdog heartbeat emitted while the SSE stream is silent.
    # Hosts that reset their tool-call timeout on `notifications/progress` (per
    # MCP spec §Lifecycle/Timeouts) need to see one at least every host-default
    # interval. 15s sits safely under typical 60s host defaults with margin.
    opik_mcp_heartbeat_interval_s: float = 15.0

    # Hard ceiling on how long the pod can be silent (no real SSE event) before
    # ask_ollie aborts the call. Without this, a stalled pod combined with a
    # working heartbeat keeps the host hanging indefinitely — the heartbeat
    # would happily reset the host's timeout forever. 300s covers cold SDK
    # roundtrips and large test-suite evals while still bounding the worst case.
    # Set to 0 to disable (debug only).
    opik_mcp_stream_idle_timeout_s: float = 300.0

    opik_mcp_dev_token: str = "dev-token-123"
    opik_mcp_log_level: str = "INFO"
    opik_mcp_transport: str = "stdio"
    opik_mcp_host: str = "127.0.0.1"
    opik_mcp_port: int = 8080
    opik_mcp_reload: bool = False

    # YOLO mode toggle. "enabled" (default) auto-approves every pod
    # `confirm_required` (audit row written before the confirm POST). "disabled"
    # surfaces each confirm_required to the host LLM as a typed pod-stream error
    # carrying the pod-supplied `summary`; no audit row, no confirm POST. The
    # user can re-issue manually after deciding. Validated strictly so a typo
    # ("disable", "off") fails loudly at startup rather than silently leaving
    # auto-approval on when the user thought they opted out.
    opik_mcp_auto_approve: Literal["enabled", "disabled"] = "enabled"

    # Maximum seconds to wait for the user to answer an elicitation prompt
    # (currently used only by `ask_ollie` mid-stream tool-call confirms).
    # Timeout is treated as a deny — the safer default; the user can always
    # re-issue. 60s matches typical chat-UI attention spans without holding
    # the host's tool-call slot open indefinitely.
    opik_mcp_elicit_timeout_seconds: float = 60.0

    @field_validator("opik_mcp_auto_approve", mode="before")
    @classmethod
    def _lowercase_toggle(cls, v: Any) -> Any:
        return v.lower() if isinstance(v, str) else v

    @field_validator("comet_workspace_id", mode="before")
    @classmethod
    def _validate_workspace_uuid(cls, v: Any) -> Any:
        # Loud at startup beats silently mis-stamping every event with a
        # garbage workspace id — see comment on the field.
        if v is None or v == "":
            return None
        try:
            UUID(str(v))
        except ValueError as e:
            raise ValueError(
                f"COMET_WORKSPACE_ID={v!r} is not a valid UUID; "
                "set it to the workspace UUID or leave it unset"
            ) from e
        return str(v)

    # Analytics / telemetry
    opik_mcp_analytics_enabled: bool = True
    opik_mcp_analytics_url: str = "https://stats.comet.com/notify/event/"
    opik_mcp_analytics_environment: str = "prod"
    opik_mcp_analytics_connect_timeout_s: float = 5.0
    opik_mcp_analytics_total_timeout_s: float = 10.0
    # Propagated as `event_properties.source`. The comet-stats receiver uses
    # this to mark `on_prem=False` and skip IP enrichment; matches the
    # `OLLIE_SOURCE` convention (codepanels injects `comet.com` at deploy
    # time for ollie-assist). Defaulting to `comet.com` reflects that
    # opik-mcp Phase 1 ships as a cloud-Comet client. On-prem installs
    # should override to "" (omit the field) or to their own domain.
    opik_mcp_analytics_source: str = "comet.com"

    # Sentry error tracking. Separate from analytics on purpose: analytics
    # ships low-cardinality buckets to the BI funnel; Sentry ships stack
    # traces for the bugs that need a human. Users can opt out of either
    # one independently (mirrors the opik SDK's `sentry_enable` flag).
    opik_mcp_sentry_enabled: bool = True

    # Hardcoded DSN for the opik-mcp Sentry project. Public ingest key, not
    # a secret — it identifies the destination project, never grants reads.
    #
    # ``ClassVar`` is deliberate: it tells pydantic-settings to treat this
    # as a class-level constant and skip env-binding. Without it, anyone
    # could redirect their (or a coworker's) crash reports to an
    # attacker-controlled Sentry project by setting ``OPIK_MCP_SENTRY_DSN``.
    # The only supported opt-out is ``OPIK_MCP_SENTRY_ENABLED=false``.
    opik_mcp_sentry_dsn: ClassVar[str] = (
        "https://0b191296a0c2e1369da34e7d8fa85322@o168229.ingest.us.sentry.io/4511450607910912"
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def require_ollie_config(settings: Settings) -> tuple[str, str]:
    if not settings.opik_api_key:
        raise MissingConfigError("OPIK_API_KEY is required to use ask_ollie")
    # Workspace is optional — fall back to "default" (Opik SDK convention).
    # Cloud pod discovery for a non-"default" account will surface its own
    # clear error downstream, which beats a hard config failure here.
    return settings.opik_api_key, settings.comet_workspace or DEFAULT_WORKSPACE
