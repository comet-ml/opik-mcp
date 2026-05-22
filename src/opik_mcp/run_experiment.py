"""`run_experiment` MCP tool orchestrator.

Fire-and-return: submits an experiment-execution request to opik-backend's
`/v1/private/experiments/execute` endpoint and returns the created
experiment IDs immediately. Experiments are long-running async jobs; the
caller checks status later via `read("experiment", id)`.
"""

from __future__ import annotations

import logging
from urllib.parse import quote

import httpx

from opik_mcp.opik_client import (
    OpikAuthError,
    OpikClient,
    OpikNotFoundError,
    OpikPermissionError,
    OpikServerError,
    OpikValidationError,
)
from opik_mcp.run_experiment_models import (
    ExperimentHandle,
    RunExperimentConfig,
    RunExperimentResult,
)

logger = logging.getLogger("opik_mcp.run_experiment")


def _summary_url(*, comet_base_url: str, workspace: str, experiment_ids: list[str]) -> str:
    """Mirror the FE `useNavigateToExperiment` compare URL shape."""
    base = comet_base_url.rstrip("/")
    ids = ",".join(experiment_ids)
    # Percent-encode the bracketed list — raw ``[`` / ``]`` in a query value is
    # not RFC 3986 compliant and some proxies/WAFs will reject or normalize it.
    experiments_param = quote(f"[{ids}]", safe="")
    return f"{base}/{quote(workspace)}/redirect/experiments?experiments={experiments_param}"


def _raise_for_execute_status(resp: httpx.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    body_excerpt = (resp.text or "")[:500]
    if resp.status_code == 401:
        raise OpikAuthError(
            "Opik rejected the experiment execute request (401). Check OPIK_API_KEY."
        )
    if resp.status_code == 403:
        raise OpikPermissionError(
            "Opik rejected the experiment execute request (403). The API key is "
            "valid but lacks permission for this workspace. Check COMET_WORKSPACE."
        )
    if resp.status_code == 404:
        raise OpikNotFoundError(f"Test suite not found (404) — {body_excerpt}")
    if resp.status_code in (400, 422):
        raise OpikValidationError(
            f"Opik rejected the experiment config ({resp.status_code}) — {body_excerpt}"
        )
    raise OpikServerError(
        f"Opik server error ({resp.status_code}) during experiment execute — {body_excerpt}"
    )


async def run_experiment_impl(
    *,
    config: RunExperimentConfig,
    client: OpikClient,
    comet_base_url: str,
    workspace: str,
) -> RunExperimentResult:
    """Submit an experiment-execution request, return the created handles.

    Workspace + OPIK base are already bound to ``client`` (constructor
    injection); we accept ``comet_base_url`` + ``workspace`` again only to
    build the summary URL (which points at the Comet UI host, not the API).
    """
    resp = await client.execute_experiment(config.to_wire_body())
    _raise_for_execute_status(resp)
    try:
        envelope = resp.json()
    except ValueError as exc:
        raise OpikServerError(
            "Opik returned a non-JSON body for POST /v1/private/experiments/execute: "
            f"{resp.text[:200]!r}"
        ) from exc

    handles = [ExperimentHandle.model_validate(e) for e in envelope.get("experiments", [])]
    total_items = int(envelope.get("total_items") or 0)
    experiment_ids = [str(h.experiment_id) for h in handles]
    prompt_indexes = [h.prompt_index for h in handles]
    summary_url = _summary_url(
        comet_base_url=comet_base_url, workspace=workspace, experiment_ids=experiment_ids
    )
    return RunExperimentResult(
        experiment_ids=experiment_ids,
        prompt_indexes=prompt_indexes,
        total_items=total_items,
        summary_url=summary_url,
    )
