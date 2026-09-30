"""Structural types the read and list tools depend on instead of the concrete client."""

from __future__ import annotations

from typing import Any, Literal, Protocol

# The client's own vocabulary, declared with the contract rather than with the
# endpoint module, so this module keeps depending on nothing under ``client/``.
SpendItemKind = Literal["mcp_server", "skill", "built_in_tool"]


class OpikListClient(Protocol):
    """Structural type for the list endpoints the ``list`` tool depends on.

    Defined here so test fakes (and the read/list registry) can depend on the
    Protocol instead of the concrete client — no ``cast(OpikClient, fake)``
    gymnastics in unit tests, and the registry stays decoupled from the HTTP
    implementation.
    """

    async def list_projects(
        self,
        *,
        name: str | None = None,
        page: int = 1,
        size: int = 10,
        sorting: str | None = None,
    ) -> dict[str, Any]: ...

    async def list_traces(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_threads(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_spans(
        self,
        *,
        trace_id: str | None = None,
        project_id: str | None = None,
        project_name: str | None = None,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 100,
    ) -> dict[str, Any]: ...

    async def list_datasets(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_dataset_items(
        self,
        dataset_id: str,
        /,
        *,
        filters: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_experiments(
        self,
        *,
        name: str | None = None,
        filters: str | None = None,
        types: str | None = None,
        optimization_id: str | None = None,
        experiment_ids: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        should_truncate: bool | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_compared_dataset_items(
        self,
        dataset_id: str,
        /,
        *,
        experiment_ids: list[str],
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_compared_output_columns(
        self, dataset_id: str, /, *, experiment_ids: list[str]
    ) -> dict[str, Any]: ...

    async def list_prompts(
        self, *, name: str | None = None, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_prompt_versions(
        self, prompt_id: str, /, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_agent_insights_issues(
        self,
        *,
        project_id: str,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        page: int = 1,
        size: int = 10,
    ) -> dict[str, Any]: ...

    async def list_project_score_names(self, project_id: str, /) -> dict[str, Any]: ...

    async def get_project_metrics(
        self,
        project_id: str,
        /,
        *,
        metric_type: str,
        interval: str,
        interval_start: str,
        interval_end: str | None = None,
        trace_filters: list[dict[str, str]] | None = None,
        span_filters: list[dict[str, str]] | None = None,
        thread_filters: list[dict[str, str]] | None = None,
        breakdown: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    async def list_project_token_usage_names(self, project_id: str, /) -> dict[str, Any]: ...

    async def list_project_activities(
        self, project_id: str, /, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def list_automation_rules(
        self, *, project_id: str, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...
    async def get_agent_insights_job(self, project_id: str, /) -> dict[str, Any]: ...

    async def get_service_toggles(self) -> dict[str, Any]: ...


class OpikReadClient(OpikListClient, Protocol):
    """Adds singleton ``get_*`` endpoints to ``OpikListClient`` for the read tool.

    The read tool calls both shapes: singletons via ``get_*`` and search/
    composite reads via ``list_*`` (e.g. name-lookup, ``list_spans`` while
    inlining a trace's spans tree).
    """

    async def get_project(self, project_id: str, /) -> dict[str, Any]: ...

    async def get_project_kpi_cards(
        self,
        project_id: str,
        /,
        *,
        entity_type: str,
        interval_start: str,
        interval_end: str | None = None,
        filters: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_trace(self, trace_id: str, /) -> dict[str, Any]: ...

    async def get_span(self, span_id: str, /) -> dict[str, Any]: ...

    async def get_dataset(self, dataset_id: str, /) -> dict[str, Any]: ...

    async def get_dataset_item(self, item_id: str, /) -> dict[str, Any]: ...

    async def get_experiment(self, experiment_id: str, /) -> dict[str, Any]: ...

    async def get_compared_stats(
        self, dataset_id: str, /, *, experiment_ids: list[str], filters: str | None = None
    ) -> dict[str, Any]: ...

    async def list_feedback_definitions(
        self, *, page: int = 1, size: int = 10
    ) -> dict[str, Any]: ...

    async def get_prompt(self, prompt_id: str, /) -> dict[str, Any]: ...

    async def get_thread(
        self,
        thread_id: str,
        /,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        should_truncate: bool = False,
    ) -> dict[str, Any]: ...

    async def get_agent_insights_issue(
        self,
        issue_id: str,
        /,
        *,
        project_id: str,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]: ...


class AiSpendClient(Protocol):
    """The AI Spend endpoints; spend entities narrow a client to this with a cast."""

    async def list_spend_users(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
        page: int = 1,
        size: int = 10,
        name: str | None = None,
        sorting: str | None = None,
    ) -> dict[str, Any]: ...

    async def list_spend_item_users(
        self,
        kind: SpendItemKind,
        item: str,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> list[Any]: ...

    async def list_spend_sessions(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        page: int = 1,
        size: int = 10,
        filters: str | None = None,
        sorting: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spend_summary(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spend_composition(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spend_lane_breakdown(
        self,
        lane_key: str,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spend_session_narrative(
        self,
        session_id: str,
        *,
        project_name: str,
        interval_start: str | None = None,
        interval_end: str | None = None,
    ) -> dict[str, Any]: ...

    async def get_spend_agents(
        self,
        *,
        project_name: str,
        interval_start: str,
        interval_end: str,
        user_email: str | None = None,
    ) -> dict[str, Any]: ...
