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
    return f"{base}/{quote(workspace)}/redirect/experiments?experiments=[{ids}]"


def _raise_for_execute_status(resp: httpx.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    body_excerpt = (resp.text or "")[:500]
    if resp.status_code in (401, 403):
        raise OpikAuthError(
            f"Opik rejected the experiment execute request ({resp.status_code}). "
            "Check OPIK_API_KEY and COMET_WORKSPACE."
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
    envelope = resp.json()

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
