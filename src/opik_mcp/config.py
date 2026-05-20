from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class MissingConfigError(RuntimeError):
    """Raised when an ask_ollie call is attempted without required env vars."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    opik_api_key: str | None = None
    comet_workspace: str | None = None
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

    # Per-write confirmation toggle. "disabled" (default) lets every validated
    # write go straight to the BE. "enabled" routes the write through the MCP
    # elicitation primitive first — the host shows a yes/no prompt to the user
    # and the call only proceeds on accept. Hosts that don't advertise the
    # elicitation capability fall back to a one-shot `ctx.warning` and proceed
    # (never silently dropped, never blocked). `prompt_version.save` with
    # `set_as_production=true` always elicits regardless of this flag because
    # production-alias flips are the highest-risk Phase 1 write.
    opik_mcp_confirm_writes: Literal["enabled", "disabled"] = "disabled"

    # Maximum seconds to wait for the user to answer an elicitation prompt.
    # Timeout is treated as a deny ("no") — the safer default; the user can
    # always re-issue the call. 60s matches typical chat-UI attention spans
    # without holding the host's tool-call slot open indefinitely.
    opik_mcp_elicit_timeout_seconds: int = 60

    @field_validator("opik_mcp_auto_approve", "opik_mcp_confirm_writes", mode="before")
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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def require_ollie_config(settings: Settings) -> tuple[str, str]:
    if not settings.opik_api_key:
        raise MissingConfigError("OPIK_API_KEY is required to use ask_ollie")
    if not settings.comet_workspace:
        raise MissingConfigError("COMET_WORKSPACE is required to use ask_ollie")
    return settings.opik_api_key, settings.comet_workspace
