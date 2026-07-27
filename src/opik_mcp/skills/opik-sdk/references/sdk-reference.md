# Opik Python SDK Reference (v2.1.14)

Auto-generated from the installed `opik` package. **Do not edit manually.**
Regenerate with: `uv run python scripts/generate_sdk_docs.py`

---

## Usage Notes

### Client initialization

Always instantiate the Opik client via `opik.Opik()` and grab its `rest_client` for direct REST calls. The pod injects `OPIK_API_KEY`, `OPIK_WORKSPACE`, and `OPIK_URL_OVERRIDE` into the subprocess environment, and `opik.Opik()` reads them automatically.

```python
import opik
client = opik.Opik()       # picks up env-injected key, workspace, and url_override
rest = client.rest_client  # sync OpikApi instance for low-level REST calls
```

**Never** instantiate `OpikApi` / `AsyncOpikApi` directly (e.g. `from opik.rest_api import OpikApi; client = OpikApi()`). Bare instantiation skips the env-injected configuration and tries to dial the public Opik backend, which the pod cannot reach. Symptom: `httpx.ConnectError: All connection attempts failed`.

This applies to every recipe in the references.

### Experiments and blueprint_id

Every `evaluate()` and `run_tests()` call MUST include `blueprint_id` when the project has an agent configuration. This links the experiment to the config version. Fetch the project ID first, then the blueprint:

```python
project = client.rest_client.projects.retrieve_project(name=project_name)
# Use a specific version when the user asks for one:
bp = client.rest_client.agent_configs.get_blueprint_by_name(project_id=project.id, name="v2")
# Otherwise default to latest:
bp = client.rest_client.agent_configs.get_latest_blueprint(project.id)
evaluate(..., blueprint_id=bp.id)
```
---

## `opik.api_objects.opik_client`

### `Opik`

```python
Opik(project_name: Optional[str] = None, workspace: Optional[str] = None, host: Optional[str] = None, api_key: Optional[str] = None, batching: bool = True, _use_batching: bool = False, _show_misconfiguration_message: bool = True) -> None
```

- `__init__(self, project_name: Optional[str] = None, workspace: Optional[str] = None, host: Optional[str] = None, api_key: Optional[str] = None, batching: bool = True, _use_batching: bool = False, _show_misconfiguration_message: bool = True) -> None` — Initialize an Opik object that can be used to log traces and spans manually to Opik server.
- `auth_check(self) -> None` — Checks if current API key user has an access to the configured workspace and its content.
- `config` -> opik.config.OpikConfig *(property)* — Returns:
    OpikConfig: Read-only copy of the configuration of the Opik client.
- `copy_traces(self, project_name: str, destination_project_name: str, delete_original_project: bool = False) -> None` — Copy traces from one project to another. This method will copy all traces in a source project
to the destination project. Optionally, you can also delete these traces from the source project.
- `create_chat_prompt(self, name: str, messages: List[Dict[str, Any]], metadata: Optional[Dict[str, Any]] = None, type: opik.api_objects.prompt.types.PromptType = <PromptType.MUSTACHE: 'mustache'>, id: Optional[str] = None, description: Optional[str] = None, change_description: Optional[str] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> opik.api_objects.prompt.chat.chat_prompt.ChatPrompt` — Creates a new chat prompt with the given name and message templates.
If a chat prompt with the same name already exists, it will create a new version if the messages differ.
- `create_config(self, config: opik.api_objects.agent_config.base.Config, project_name: Optional[str] = None, description: Optional[str] = None) -> str` — Write a config version to the backend unconditionally.
- `create_dashboard(self, name: str, type: Union[opik.api_objects.dashboard.types.DashboardType, str, NoneType] = None, description: Optional[str] = None, project_name: Optional[str] = None, project_id: Optional[str] = None, sections: Optional[List[Union[opik.api_objects.dashboard.types.DashboardSection, Dict[str, Any]]]] = None) -> opik.api_objects.dashboard.dashboard.Dashboard` — Create a new dashboard.
- `create_dataset(self, name: str, description: Optional[str] = None, project_name: Optional[str] = None) -> opik.api_objects.dataset.dataset.Dataset` — Create a new dataset.
- `create_environment(self, name: str, description: Optional[str] = None, color: Optional[str] = None) -> opik.rest_api.types.environment_public.EnvironmentPublic` — Create a new environment in the current workspace.
- `create_experiment(self, dataset_name: str, name: Optional[str] = None, experiment_config: Optional[Dict[str, Any]] = None, prompt: Optional[opik.api_objects.prompt.base_prompt.BasePrompt] = None, prompts: Optional[List[opik.api_objects.prompt.base_prompt.BasePrompt]] = None, type: Literal['regular', 'trial', 'mini-batch'] = 'regular', evaluation_method: Literal['dataset', 'evaluation_suite'] = 'dataset', optimization_id: Optional[str] = None, tags: Optional[List[str]] = None, dataset_version_id: Optional[str] = None, project_name: Optional[str] = None) -> opik.api_objects.experiment.experiment.Experiment` — Creates a new experiment using the given dataset name and optional parameters.
- `create_optimization(self, dataset_name: str, objective_name: str, name: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None, optimization_id: Optional[str] = None, project_name: Optional[str] = None) -> opik.api_objects.optimization.optimization.Optimization`
- `create_prompt(self, name: str, prompt: str, metadata: Optional[Dict[str, Any]] = None, type: opik.api_objects.prompt.types.PromptType = <PromptType.MUSTACHE: 'mustache'>, id: Optional[str] = None, description: Optional[str] = None, change_description: Optional[str] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> opik.api_objects.prompt.text.prompt.Prompt` — Creates a new text prompt with the given name and template.
If a text prompt with the same name already exists, it will create a new version of the existing prompt if the templates differ.
- `create_test_suite(self, name: str, description: Optional[str] = None, global_assertions: Optional[List[str]] = None, global_execution_policy: Optional[opik.api_objects.dataset.execution_policy.ExecutionPolicy] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> opik.api_objects.dataset.test_suite.test_suite.TestSuite` — Create a new test suite for regression testing.
- `create_threads_annotation_queue(self, name: str, project_name: Optional[str] = None, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None) -> opik.api_objects.annotation_queue.annotation_queue.ThreadsAnnotationQueue` — Create a new annotation queue for threads.
- `create_traces_annotation_queue(self, name: str, project_name: Optional[str] = None, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None) -> opik.api_objects.annotation_queue.annotation_queue.TracesAnnotationQueue` — Create a new annotation queue for traces.
- `delete_annotation_queue(self, queue_id: str) -> None` — Delete an annotation queue by its ID.
- `delete_dashboard(self, dashboard_id: str) -> None` — Delete a dashboard by id.
- `delete_dataset(self, name: str, project_name: Optional[str] = None) -> None` — Delete dataset by name
- `delete_environment(self, name: str) -> None` — Delete an environment by name. No-op if no matching environment exists.
- `delete_optimizations(self, ids: List[str]) -> None`
- `delete_span_feedback_score(self, span_id: str, name: str) -> None` — Deletes a feedback score associated with a specific span.
- `delete_test_suite(self, name: str, project_name: Optional[str] = None) -> None` — Delete a test suite by name.
- `delete_trace_feedback_score(self, trace_id: str, name: str) -> None` — Deletes a feedback score associated with a specific trace.
- `end(self, timeout: Optional[int] = None, *, flush: bool = True) -> None` — End the Opik session and submit all pending messages.
- `flush(self, timeout: Optional[int] = None) -> bool` — Flush the streamer to ensure all messages are sent.
- `get_attachment_client(self) -> opik.api_objects.attachment.client.AttachmentClient` — Creates and provides an instance of the ``AttachmentClient`` tied to the current context.
- `get_chat_prompt(self, name: str, commit: Optional[str] = None, project_name: Optional[str] = None, no_cache: bool = False, version: Optional[str] = None, environment: Optional[str] = None) -> Optional[opik.api_objects.prompt.chat.chat_prompt.ChatPrompt]` — Retrieve a chat prompt by name, optionally targeting a specific ``version``.
- `get_chat_prompt_history(self, name: str, search: Optional[str] = None, filter_string: Optional[str] = None, project_name: Optional[str] = None) -> List[opik.api_objects.prompt.chat.chat_prompt.ChatPrompt]` — Retrieve all chat prompt versions history for a given prompt name.
- `get_dashboard(self, dashboard_id: str) -> opik.api_objects.dashboard.dashboard.Dashboard` — Get a dashboard by id.
- `get_dashboards(self, name: Optional[str] = None, project_id: Optional[str] = None, max_results: int = 100, sorting: Optional[str] = None, filters: Optional[str] = None) -> List[opik.api_objects.dashboard.dashboard.Dashboard]` — Get dashboards in the workspace.
- `get_dataset(self, name: str, project_name: Optional[str] = None) -> opik.api_objects.dataset.dataset.Dataset` — Get dataset by name
- `get_dataset_experiments(self, dataset_name: str, max_results: int = 100, project_name: Optional[str] = None) -> List[opik.api_objects.experiment.experiment.Experiment]` — Returns all experiments up to the specified limit.
- `get_datasets(self, max_results: int = 100, sync_items: bool = False, project_name: Optional[str] = None) -> List[opik.api_objects.dataset.dataset.Dataset]` — Returns all datasets up to the specified limit.
- `get_environments(self) -> List[opik.rest_api.types.environment_public.EnvironmentPublic]` — List environments in the current workspace.
- `get_experiment_by_id(self, id: str) -> opik.api_objects.experiment.experiment.Experiment` — Returns an existing experiment by its id.
- `get_experiments_by_name(self, name: str, project_name: Optional[str] = None) -> List[opik.api_objects.experiment.experiment.Experiment]` — Returns a list of existing experiments containing the given string in their name.
Search is case-insensitive.
- `get_experiments_client(self) -> opik.api_objects.experiment.experiments_client.ExperimentsClient` — Retrieves an instance of `ExperimentsClient`.
- `get_optimization_by_id(self, id: str) -> opik.api_objects.optimization.optimization.Optimization`
- `get_or_create_config(self, *, fallback: Optional[opik.api_objects.agent_config.base.Config] = None, project_name: Optional[str] = None, env: Optional[str] = None, version: Optional[str] = None, timeout_in_seconds: Optional[int] = 5) -> opik.api_objects.agent_config.base.Config` — Fetch a config from the backend, optionally auto-creating from a fallback.
- `get_or_create_dataset(self, name: str, description: Optional[str] = None, project_name: Optional[str] = None) -> opik.api_objects.dataset.dataset.Dataset` — Get an existing dataset by name or create a new one if it does not exist.
- `get_or_create_test_suite(self, name: str, description: Optional[str] = None, global_assertions: Optional[List[str]] = None, global_execution_policy: Optional[opik.api_objects.dataset.execution_policy.ExecutionPolicy] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> opik.api_objects.dataset.test_suite.test_suite.TestSuite` — Get an existing test suite by name or create a new one if it does not exist.
- `get_project(self, id: str) -> opik.rest_api.types.project_public.ProjectPublic` — Fetches a project by its unique identifier.
- `get_project_url(self, project_name: Optional[str] = None) -> str` — Returns a URL to the project in the current workspace.
This method does not make any requests or perform any checks (e.g. that the project exists).
It only builds a URL string based on the data provided.
- `get_prompt(self, name: str, commit: Optional[str] = None, project_name: Optional[str] = None, no_cache: bool = False, version: Optional[str] = None, environment: Optional[str] = None) -> Optional[opik.api_objects.prompt.text.prompt.Prompt]` — Retrieve a text prompt by name, optionally targeting a specific ``version``.
- `get_prompt_history(self, name: str, search: Optional[str] = None, filter_string: Optional[str] = None, project_name: Optional[str] = None) -> List[opik.api_objects.prompt.text.prompt.Prompt]` — Retrieve all text prompt versions history for a given prompt name.
- `get_prompts_client(self) -> opik.api_objects.prompt.client.PromptClient` — Retrieves an instance of `PromptClient` for bulk prompt operations.
- `get_span_content(self, id: str) -> opik.rest_api.types.span_public.SpanPublic` — Args:
    id (str): span id
Returns:
    span_public.SpanPublic: pydantic model object with all the data associated with the span found.
    Raises an error if span was not found.
- `get_test_suite(self, name: str, project_name: Optional[str] = None) -> opik.api_objects.dataset.test_suite.test_suite.TestSuite` — Get an existing test suite by name.
- `get_test_suite_experiments(self, name: str, max_results: int = 100, project_name: Optional[str] = None) -> List[opik.api_objects.experiment.experiment.Experiment]` — Returns all experiments for a test suite.
- `get_test_suites(self, max_results: int = 100, project_name: Optional[str] = None) -> List[opik.api_objects.dataset.test_suite.test_suite.TestSuite]` — Returns all test suites up to the specified limit.
- `get_threads_annotation_queue(self, queue_id: str) -> opik.api_objects.annotation_queue.annotation_queue.ThreadsAnnotationQueue` — Get a threads annotation queue by its ID.
- `get_threads_annotation_queues(self, project_name: Optional[str] = None, max_results: int = 1000) -> List[opik.api_objects.annotation_queue.annotation_queue.ThreadsAnnotationQueue]` — Get all threads annotation queues for a project.
- `get_threads_client(self) -> opik.api_objects.threads.threads_client.ThreadsClient` — Creates and provides an instance of the ``ThreadsClient`` tied to the current context.
- `get_trace_content(self, id: str) -> opik.rest_api.types.trace_public.TracePublic` — Args:
    id (str): trace id
Returns:
    trace_public.TracePublic: pydantic model object with all the data associated with the trace found.
    Raises an error if trace was not found.
- `get_traces_annotation_queue(self, queue_id: str) -> opik.api_objects.annotation_queue.annotation_queue.TracesAnnotationQueue` — Get a traces annotation queue by its ID.
- `get_traces_annotation_queues(self, project_name: Optional[str] = None, max_results: int = 1000) -> List[opik.api_objects.annotation_queue.annotation_queue.TracesAnnotationQueue]` — Get all traces annotation queues for a project.
- `log_assertion_results(self, assertion_results: List[opik.types.BatchAssertionResultDict], project_name: Optional[str] = None) -> None` — Log assertion results for traces via the dedicated assertion-results
ingestion endpoint.
- `log_spans_feedback_scores(self, scores: List[opik.types.BatchFeedbackScoreDict], project_name: Optional[str] = None) -> None` — Log feedback scores for spans.
- `log_threads_feedback_scores(self, scores: List[opik.types.BatchFeedbackScoreDict], project_name: Optional[str] = None) -> None` — Log feedback scores for threads.
- `log_traces_feedback_scores(self, scores: List[opik.types.BatchFeedbackScoreDict], project_name: Optional[str] = None) -> None` — Log feedback scores for traces.
- `project_name` -> str *(property)* — This property retrieves the name of the project associated with the instance.
It is a read-only property.
- `queue_attachment_upload(self, entity_type: Literal['trace', 'span'], entity_id: str, project_name: str, file_path: str, file_name: Optional[str] = None, mime_type: Optional[str] = None) -> None` — Queue a local file for background upload as an attachment via the streamer.
- `rest_client` -> opik.rest_api.client.OpikApi *(property)* — Provides direct access to the underlying REST API client.
- `search_prompts(self, filter_string: Optional[str] = None, project_name: Optional[str] = None) -> List[Union[opik.api_objects.prompt.text.prompt.Prompt, opik.api_objects.prompt.chat.chat_prompt.ChatPrompt]]` — Retrieve the latest prompt versions (both string and chat prompts) for the given search parameters.
- `search_spans(self, project_name: Optional[str] = None, trace_id: Optional[str] = None, filter_string: Optional[str] = None, max_results: int = 1000, truncate: bool = True, exclude: Optional[List[str]] = None, wait_for_at_least: Optional[int] = None, wait_for_timeout: int = 100) -> List[opik.rest_api.types.span_public.SpanPublic]` — Search for spans in the given trace. This allows you to search spans based on the span input, output,
metadata, tags, etc. or based on the trace ID. Also, you can wait for at least a certain number of spans
to be found before returning within the specified timeout. If wait_for_at_least number of spans are not found
within the specified timeout, an exception will be raised.
- `search_threads(self, project_name: Optional[str] = None, filter_string: Optional[str] = None, max_results: int = 1000, truncate: bool = True) -> List[opik.rest_api.types.trace_thread.TraceThread]` — Search for threads in a given project based on specific criteria.
- `search_traces(self, project_name: Optional[str] = None, filter_string: Optional[str] = None, max_results: int = 1000, truncate: bool = True, exclude: Optional[List[str]] = None, wait_for_at_least: Optional[int] = None, wait_for_timeout: int = 100) -> List[opik.rest_api.types.trace_public.TracePublic]` — Search for traces in the given project. Optionally, you can wait for at least a certain number of traces
to be found before returning within the specified timeout. If wait_for_at_least number of traces are not found
within the specified timeout, an exception will be raised.
- `set_config_env(self, *, project_name: Optional[str] = None, version: str, env: str) -> None` — Tag a specific config version with an environment name.
- `set_prompt_environments(self, prompt_name: str, environments: List[str], *, version: Optional[str] = None, project_name: Optional[str] = None) -> None` — Replace the full set of environments owned by a prompt version.
- `span(self, trace_id: Optional[str] = None, id: Optional[str] = None, parent_span_id: Optional[str] = None, name: Optional[str] = None, type: Literal['general', 'tool', 'llm', 'guardrail'] = 'general', start_time: Optional[datetime.datetime] = None, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, feedback_scores: Optional[List[opik.types.FeedbackScoreDict]] = None, project_name: Optional[str] = None, model: Optional[str] = None, provider: Union[str, opik.types.LLMProvider, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None) -> opik.api_objects.span.span_client.Span` — Create and log a new span.
- `trace(self, id: Optional[str] = None, name: Optional[str] = None, start_time: Optional[datetime.datetime] = None, end_time: Optional[datetime.datetime] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, feedback_scores: Optional[List[opik.types.FeedbackScoreDict]] = None, project_name: Optional[str] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, thread_id: Optional[str] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None, environment: Optional[str] = None, **ignored_kwargs: Any) -> opik.api_objects.trace.trace_client.Trace` — Create and log a new trace.
- `update_environment(self, name: str, description: Optional[str] = None, color: Optional[str] = None) -> opik.rest_api.types.environment_public.EnvironmentPublic` — Update the description and/or color of an environment, identified by name.
- `update_experiment(self, id: str, name: Optional[str] = None, experiment_config: Optional[Dict[str, Any]] = None) -> None` — Update an experiment's name and/or configuration.
- `update_span(self, id: str, trace_id: str, parent_span_id: Optional[str], project_name: str, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[str, opik.types.LLMProvider, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None) -> None` — Update the attributes of an existing span.
- `update_trace(self, trace_id: str, project_name: str, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[Any]] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, thread_id: Optional[str] = None) -> None` — Update the trace attributes.

### `get_client_cached() -> opik.api_objects.opik_client.Opik`

### `get_current_client_raw() -> Optional[opik.api_objects.opik_client.Opik]`
> Return the active Opik client without auto-creating one.
>
> Resolution order:
> 1. Context-local client (set via ``set_global_client(client, context_wise=True)``)
> 2. Global singleton (set via ``set_global_client(client)``)
> 3. ``None`` if no client has been set

### `get_global_client() -> opik.api_objects.opik_client.Opik`
> Get the active Opik client, creating one if needed.
>
> Resolution order:
> 1. Context-local client (set via ``set_global_client(client, context_wise=True)``)
> 2. Global singleton (set via ``set_global_client(client)``)
> 3. Auto-created default client (created on first call)

### `reset_global_client(end_client: bool = True) -> None`
> Clear the active Opik client.
>
> Args:
>     end_client: If True (default), calls ``.end()`` on the global singleton
>         before clearing it. Set to False when the caller manages the client
>         lifecycle independently.

### `set_global_client(client: opik.api_objects.opik_client.Opik, context_wise: bool = False) -> None`
> Set the active Opik client.
>
> Args:
>     client: The Opik client instance to use.
>     context_wise: If True, sets the client for the current context only
>         (thread-safe, async-safe). If False, replaces the global singleton.

## `opik.api_objects.dataset.dataset`

### `Dataset`

> Abstract base class providing export operations for dataset items.

```python
Dataset(name: str, description: Optional[str], project_name: Optional[str], rest_client: opik.rest_api.client.OpikApi, dataset_items_count: Optional[int] = None, client: Optional[Any] = None) -> None
```

- `__init__(self, name: str, description: Optional[str], project_name: Optional[str], rest_client: opik.rest_api.client.OpikApi, dataset_items_count: Optional[int] = None, client: Optional[Any] = None) -> None` — A Dataset object. This object should not be created directly, instead use :meth:`opik.Opik.create_dataset` or :meth:`opik.Opik.get_dataset`.
- `clear(self) -> None` — Delete all items from the given dataset. A new dataset version will be created.
- `dataset_items_count` -> Optional[int] *(property)* — The total number of items in the dataset.
- `delete(self, items_ids: List[str]) -> None` — Delete items from the dataset. A new dataset version will be created.
- `description` -> Optional[str] *(property)* — The description of the dataset.
- `from_public(cls, dataset_fern: opik.rest_api.types.dataset_public.DatasetPublic, project_name: str, rest_client: opik.rest_api.client.OpikApi, client: Optional[Any] = None) -> 'Dataset'` — Build a Dataset from a backend response, resolving the actual project.
- `get_current_version_name(self) -> Optional[str]` — Get the current version name of the dataset.
- `get_evaluators(self, evaluator_model: Optional[str] = None) -> List[Any]` — Get suite-level evaluators from the current dataset version.
- `get_execution_policy(self) -> opik.api_objects.dataset.execution_policy.ExecutionPolicy` — Get suite-level execution policy from the current dataset version.
- `get_items(self, nb_samples: Optional[int] = None, filter_string: Optional[str] = None) -> List[Dict[str, Any]]` — Retrieve dataset items as a list of dictionaries.
- `get_tags(self) -> List[str]` — Get the tags for this dataset.
- `get_version_info(self) -> Optional[opik.rest_api.types.dataset_version_public.DatasetVersionPublic]` — Get version information for the current (latest) dataset version.
- `get_version_view(self, version_name: str) -> opik.api_objects.dataset.dataset.DatasetVersion` — Get a read-only view of a specific dataset version.
- `insert(self, items: Sequence[Dict[str, Any]]) -> None` — Insert new items into the dataset. A new dataset version will be created.
- `insert_from_json(self, json_array: str, keys_mapping: Optional[Dict[str, str]] = None, ignore_keys: Optional[List[str]] = None) -> None` — Args:
    json_array: json string of format: "[{...}, {...}, {...}]" where every dictionary
        is to be transformed into dataset item
    keys_mapping: dictionary that maps json keys to item fields names
        Example: {'Expected output': 'expected_output'}
    ignore_keys: if your json dicts contain keys that are not needed for DatasetItem
        construction - pass them as ignore_keys argument
- `insert_from_pandas(self, dataframe: 'pd.DataFrame', keys_mapping: Optional[Dict[str, str]] = None, ignore_keys: Optional[List[str]] = None) -> None` — Requires: `pandas` library to be installed.
- `name` -> str *(property)* — The name of the dataset.
- `project_name` -> Optional[str] *(property)* — The name of the project this dataset belongs to.
- `read_jsonl_from_file(self, file_path: str, keys_mapping: Optional[Dict[str, str]] = None, ignore_keys: Optional[List[str]] = None) -> None` — Read JSONL from a file and insert it into the dataset.
- `to_json(self) -> str` — Convert the dataset items to a JSON string.
- `to_pandas(self) -> 'pd.DataFrame'` — Convert the dataset items to a pandas DataFrame.
- `update(self, items: List[Dict[str, Any]]) -> None` — Update existing items in the dataset.

### `DatasetExportOperations`

> Abstract base class providing export operations for dataset items.

```python
DatasetExportOperations()
```

- `get_items(self, nb_samples: Optional[int] = None, filter_string: Optional[str] = None) -> List[Dict[str, Any]]` — Retrieve dataset items as a list of dictionaries.
- `get_version_info(self) -> Optional[opik.rest_api.types.dataset_version_public.DatasetVersionPublic]` — Get version information for experiment association.
- `to_json(self) -> str` — Convert the dataset items to a JSON string.
- `to_pandas(self) -> 'pd.DataFrame'` — Convert the dataset items to a pandas DataFrame.

### `DatasetVersion`

> A read-only view of a specific dataset version.

```python
DatasetVersion(dataset_name: str, dataset_id: str, rest_client: opik.rest_api.client.OpikApi, version_info: opik.rest_api.types.dataset_version_public.DatasetVersionPublic, project_name: Optional[str], client: Optional[Any] = None) -> None
```

- `__init__(self, dataset_name: str, dataset_id: str, rest_client: opik.rest_api.client.OpikApi, version_info: opik.rest_api.types.dataset_version_public.DatasetVersionPublic, project_name: Optional[str], client: Optional[Any] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `change_description` -> Optional[str] *(property)* — Description of changes in this version.
- `created_at` -> Optional[datetime.datetime] *(property)* — Timestamp when this version was created.
- `created_by` -> Optional[str] *(property)* — User who created this version.
- `dataset_id` -> str *(property)* — The unique identifier of the dataset this version belongs to.
- `dataset_items_count` -> Optional[int] *(property)* — Total number of items in this version (alias for items_total).
- `dataset_name` -> str *(property)* — The name of the dataset this version belongs to.
- `get_evaluators(self, evaluator_model: Optional[str] = None) -> List[Any]` — Get suite-level evaluators for this dataset version.
- `get_execution_policy(self) -> opik.api_objects.dataset.execution_policy.ExecutionPolicy` — Get the execution policy for this dataset version.
- `get_items(self, nb_samples: Optional[int] = None, filter_string: Optional[str] = None) -> List[Dict[str, Any]]` — Retrieve dataset items as a list of dictionaries.
- `get_version_info(self) -> Optional[opik.rest_api.types.dataset_version_public.DatasetVersionPublic]` — Get version information for this specific dataset version.
- `id` -> str *(property)* — The unique identifier of the dataset this version belongs to (alias for dataset_id).
- `is_latest` -> Optional[bool] *(property)* — Whether this is the latest version of the dataset.
- `items_added` -> Optional[int] *(property)* — Number of items added since the previous version.
- `items_deleted` -> Optional[int] *(property)* — Number of items deleted since the previous version.
- `items_modified` -> Optional[int] *(property)* — Number of items modified since the previous version.
- `items_total` -> Optional[int] *(property)* — Total number of items in this version.
- `name` -> str *(property)* — The name of the dataset this version belongs to (alias for dataset_name).
- `project_name` -> Optional[str] *(property)* — The name of the project this dataset belongs to.
- `tags` -> Optional[List[str]] *(property)* — Tags associated with this version.
- `to_json(self) -> str` — Convert the dataset items to a JSON string.
- `to_pandas(self) -> 'pd.DataFrame'` — Convert the dataset items to a pandas DataFrame.
- `version_hash` -> Optional[str] *(property)* — The unique hash identifier of this version.
- `version_id` -> Optional[str] *(property)* — The unique identifier of this specific version.
- `version_name` -> Optional[str] *(property)* — The sequential version name (e.g., 'v1', 'v2').

**Constants:**
- `TYPE_CHECKING = False`

## `opik.api_objects.dataset.dataset_item`

### `DatasetItem`

> A DatasetItem object representing an item in a dataset.

**Fields:**
- `id: str` *(required)*
- `trace_id: Optional[str] = None`
- `span_id: Optional[str] = None`
- `source: str = 'sdk'`
- `description: Optional[str] = None`
- `evaluators: Optional[List[opik.api_objects.dataset.dataset_item.EvaluatorItem]] = None`
- `execution_policy: Optional[opik.api_objects.dataset.dataset_item.ExecutionPolicyItem] = None`

- `content_hash(self) -> str`
- `get_content(self, include_id: bool = False) -> Dict[str, Any]` — Get the data content of the dataset item (extra fields).

### `EvaluatorItem`

> An evaluator configuration for a dataset item.

**Fields:**
- `name: str` *(required)*
- `type: str` *(required)*
- `config: Dict[str, Any]` *(required)*


### `ExecutionPolicyItem`

> Execution policy for a dataset item.

**Fields:**
- `runs_per_item: Optional[int] = None`
- `pass_threshold: Optional[int] = None`


## `opik.api_objects.dataset.test_suite.test_suite`

### `TestSuite`

> A pre-configured regression test suite for LLM applications.

```python
TestSuite(name: 'str', dataset_: 'dataset.Dataset', client: "Optional['opik_client_module.Opik']" = None)
```

- `__init__(self, name: 'str', dataset_: 'dataset.Dataset', client: "Optional['opik_client_module.Opik']" = None)` — Internal constructor — not part of the public API.
- `clear(self) -> 'None'` — Delete all items from the test suite.
- `delete(self, items_ids: 'List[str]') -> 'None'` — Delete items from the test suite by their IDs.
- `description` -> 'Optional[str]' *(property)* — The description of the test suite.
- `get_current_version_name(self) -> 'Optional[str]'` — Get the current version name of the test suite.
- `get_global_assertions(self) -> 'List[str]'` — Get the suite-level assertions.
- `get_global_execution_policy(self) -> 'execution_policy.ExecutionPolicy'` — Get the suite-level execution policy.
- `get_items(self, nb_samples: 'Optional[int]' = None, filter_string: 'Optional[str]' = None) -> 'List[suite_types.TestSuiteItem]'` — Retrieve suite items as a list of dictionaries.
- `get_tags(self) -> 'List[str]'` — Get the tags for the suite.
- `get_version_info(self) -> 'Optional[dataset_version_public.DatasetVersionPublic]'` — Get version information for the current (latest) version.
- `get_version_view(self, version_name: 'str') -> 'TestSuiteVersion'` — Get a read-only view of a specific version.
- `id` -> 'str' *(property)* — The ID of the test suite.
- `insert(self, items: 'List[suite_types.TestSuiteItem]') -> 'None'` — Insert test cases into the test suite.
- `insert_from_json(self, json_array: 'str', keys_mapping: 'Optional[Dict[str, str]]' = None, ignore_keys: 'Optional[List[str]]' = None) -> 'None'` — Insert test suite items from a JSON string.
- `insert_from_jsonl_file(self, file_path: 'str', keys_mapping: 'Optional[Dict[str, str]]' = None, ignore_keys: 'Optional[List[str]]' = None) -> 'None'` — Read JSONL from a file and insert items into the test suite.
- `insert_from_pandas(self, dataframe: "'pd.DataFrame'", keys_mapping: 'Optional[Dict[str, str]]' = None, ignore_keys: 'Optional[List[str]]' = None) -> 'None'` — Insert test suite items from a pandas DataFrame.
- `items_count` -> 'Optional[int]' *(property)* — The total number of items in the test suite.
- `name` -> 'str' *(property)* — The name of the test suite.
- `project_name` -> 'Optional[str]' *(property)* — The project name associated with the test suite.
- `to_json(self) -> 'str'` — Convert the test suite items to a JSON string.
- `to_pandas(self) -> "'pd.DataFrame'"` — Convert the test suite items to a pandas DataFrame.
- `update(self, items: 'List[suite_types.TestSuiteItem]') -> 'None'` — Update existing items in the test suite.
- `update_test_settings(self, *, global_execution_policy: 'Optional[execution_policy.ExecutionPolicy]' = None, global_assertions: 'Optional[List[str]]' = None) -> 'None'` — Update the suite-level assertions and/or execution policy.

### `TestSuiteVersion`

> A read-only view of a specific test suite version.

```python
TestSuiteVersion(name: 'str', dataset_version: 'dataset.DatasetVersion', version_info: 'dataset_version_public.DatasetVersionPublic') -> 'None'
```

- `__init__(self, name: 'str', dataset_version: 'dataset.DatasetVersion', version_info: 'dataset_version_public.DatasetVersionPublic') -> 'None'` — Initialize self.  See help(type(self)) for accurate signature.
- `change_description` -> 'Optional[str]' *(property)* — Description of changes in this version.
- `created_at` -> 'Optional[datetime.datetime]' *(property)* — Timestamp when this version was created.
- `created_by` -> 'Optional[str]' *(property)* — User who created this version.
- `get_global_assertions(self) -> 'List[str]'` — Get the suite-level assertions stored in this version.
- `get_global_execution_policy(self) -> 'execution_policy.ExecutionPolicy'` — Get the suite-level execution policy stored in this version.
- `get_items(self, nb_samples: 'Optional[int]' = None, filter_string: 'Optional[str]' = None) -> 'List[suite_types.TestSuiteItem]'` — Retrieve suite items at this version as a list of dictionaries.
- `id` -> 'str' *(property)* — The dataset ID of the test suite.
- `is_latest` -> 'Optional[bool]' *(property)* — Whether this is the latest version.
- `items_added` -> 'Optional[int]' *(property)* — Number of items added since the previous version.
- `items_deleted` -> 'Optional[int]' *(property)* — Number of items deleted since the previous version.
- `items_modified` -> 'Optional[int]' *(property)* — Number of items modified since the previous version.
- `items_total` -> 'Optional[int]' *(property)* — Total number of items in this version.
- `name` -> 'str' *(property)* — The name of the test suite this version belongs to.
- `project_name` -> 'Optional[str]' *(property)* — The project name associated with the test suite.
- `tags` -> 'Optional[List[str]]' *(property)* — Tags associated with this version.
- `version_hash` -> 'Optional[str]' *(property)* — The unique hash identifier of this version.
- `version_id` -> 'Optional[str]' *(property)* — The unique identifier of this specific version.
- `version_name` -> 'Optional[str]' *(property)* — The sequential version name (e.g., 'v1', 'v2').

### `validate_task_result(result: 'Any', input_data: 'Any' = None) -> 'Dict[str, Any]'`
> Normalise the value returned by a task function into a result dict.
>
> If *result* is already a :class:`dict`, it is returned as-is (the
> supported keys are ``"input"`` and ``"output"``).
>
> For any other type the value is wrapped automatically::
>
>     {"output": result}
>
> When *input_data* is also provided the wrapper becomes::
>
>     {"input": input_data, "output": result}
>
> Args:
>     result: Value returned by the task callable.
>     input_data: Optional input that was passed to the task. Included in
>         the wrapper dict as ``"input"`` when *result* is not a dict.
>
> Returns:
>     A dict suitable for use as an experiment trace result.

**Constants:**
- `TYPE_CHECKING = False`

## `opik.api_objects.dataset.test_suite.test_suite_result`

### `ItemResult`

> Result for a single test suite item.

**Fields:**
- `dataset_item_id: str` *(required)*
- `passed: bool` *(required)*
- `has_assertions: bool` *(required)*
- `runs_passed: int` *(required)*
- `runs_total: int` *(required)*
- `configured_runs_per_item: int` *(required)*
- `pass_threshold: int` *(required)*
- `test_results: List[test_result.TestResult]` *(required)*


### `TestSuiteResult`

> Result of running a test suite.

```python
TestSuiteResult(items_passed: 'int', items_total: 'int', item_results: 'Dict[str, ItemResult]', evaluation_result_: 'evaluation_result.EvaluationResult', suite_name: 'Optional[str]' = None, total_time: 'Optional[float]' = None) -> 'None'
```

- `__init__(self, items_passed: 'int', items_total: 'int', item_results: 'Dict[str, ItemResult]', evaluation_result_: 'evaluation_result.EvaluationResult', suite_name: 'Optional[str]' = None, total_time: 'Optional[float]' = None) -> 'None'` — Initialize self.  See help(type(self)) for accurate signature.
- `all_items_passed` -> 'bool' *(property)* — Whether all items in the suite passed.
- `experiment_id` -> 'str' *(property)* — The experiment ID.
- `experiment_name` -> 'Optional[str]' *(property)* — The experiment name.
- `experiment_url` -> 'Optional[str]' *(property)* — URL to view the experiment.
- `item_results` -> 'Dict[str, ItemResult]' *(property)* — Results for each item, keyed by dataset_item_id.
- `items_passed` -> 'int' *(property)* — Number of items that passed.
- `items_total` -> 'int' *(property)* — Total number of items evaluated.
- `pass_rate` -> 'Optional[float]' *(property)* — Pass rate among items that had assertions.
- `suite_name` -> 'Optional[str]' *(property)* — The name of the test suite.
- `to_dict(self) -> 'Dict[str, Any]'` — Alias for to_report_dict().
- `to_report_dict(self) -> 'Dict[str, Any]'` — Convert the result to a structured report dictionary.
- `total_time` -> 'Optional[float]' *(property)* — Total evaluation time in seconds.

### `is_score_passed(score: 'ScoreResult') -> 'bool'`
> Determine whether a score result represents a passing assertion.

**Constants:**
- `TYPE_CHECKING = False`

## `opik.api_objects.dataset.test_suite.types`

### `TestSuiteItem`

> A test case item for a test suite.

**Fields:**
- `id: str`
- `data: Dict[str, Any]` *(required)*
- `assertions: List[str]`
- `description: str`
- `execution_policy: opik.api_objects.dataset.execution_policy.ExecutionPolicy`

### `ExecutionPolicy`

> Execution policy for test suite items.

**Fields:**
- `runs_per_item: int`
- `pass_threshold: int`


## `opik.api_objects.experiment.experiment`

### `Experiment`

```python
Experiment(id: str, name: Optional[str], dataset_name: str, rest_client: opik.rest_api.client.OpikApi, streamer: opik.message_processing.streamer.Streamer, experiments_client: opik.api_objects.experiment.experiments_client.ExperimentsClient, prompts: Optional[List[opik.api_objects.prompt.base_prompt.BasePrompt]] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> None
```

- `__init__(self, id: str, name: Optional[str], dataset_name: str, rest_client: opik.rest_api.client.OpikApi, streamer: opik.message_processing.streamer.Streamer, experiments_client: opik.api_objects.experiment.experiments_client.ExperimentsClient, prompts: Optional[List[opik.api_objects.prompt.base_prompt.BasePrompt]] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `dataset_name` -> str *(property)*
- `experiments_rest_client` -> opik.rest_api.experiments.client.ExperimentsClient *(property)*
- `get_experiment_data(self) -> opik.rest_api.types.experiment_public.ExperimentPublic`
- `get_items(self, max_results: Optional[int] = 10000, truncate: bool = False) -> List[opik.api_objects.experiment.experiment_item.ExperimentItemContent]` — Retrieves and returns a list of experiment items for this experiment.
- `id` -> str *(property)*
- `insert(self, experiment_items_references: List[opik.api_objects.experiment.experiment_item.ExperimentItemReferences]) -> None` — Creates a new experiment item by linking the existing trace and dataset item.
- `log_experiment_scores(self, score_results: List[ForwardRef('score_result.ScoreResult')]) -> None` — Log experiment-level scores to the backend.
- `name` -> str *(property)*
- `project_name` -> Optional[str] *(property)*
- `prompts` -> Optional[List[opik.api_objects.prompt.base_prompt.BasePrompt]] *(property)*
- `tags` -> Optional[List[str]] *(property)*

**Constants:**
- `TYPE_CHECKING = False`

## `opik.api_objects.experiment.experiment_item`

### `ExperimentItemContent`

**Fields:**
- `id: str` *(required)*
- `dataset_item_id: str` *(required)*
- `trace_id: str` *(required)*
- `dataset_item_data: Optional[Dict[str, Any]]` *(required)*
- `evaluation_task_output: Optional[Dict[str, Any]]` *(required)*
- `feedback_scores: List[opik.types.FeedbackScoreDict]` *(required)*
- `assertion_results: List[Dict[str, Any]]`

- `from_rest_experiment_item_compare(value: opik.rest_api.types.experiment_item_compare.ExperimentItemCompare, dataset_item_data: Optional[Dict[str, Any]] = None) -> 'ExperimentItemContent'`

### `ExperimentItemReferences`

**Fields:**
- `dataset_item_id: str` *(required)*
- `trace_id: str` *(required)*
- `project_name: Optional[str] = None`
- `execution_policy: Optional[Dict[str, Any]] = None`


## `opik.api_objects.prompt.text.prompt`

### `Prompt`

> Prompt class represents a prompt with a name, prompt text/template and commit hash.

```python
Prompt(name: str, prompt: str, metadata: Optional[Dict[str, Any]] = None, type: opik.api_objects.prompt.types.PromptType = <PromptType.MUSTACHE: 'mustache'>, validate_placeholders: bool = True, id: Optional[str] = None, description: Optional[str] = None, change_description: Optional[str] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> None
```

- `change_description` -> Optional[str] *(property)* — The description of changes in this version.
- `commit` -> Optional[str] *(property)* — Legacy commit hash of the prompt version.
- `description` -> Optional[str] *(property)* — The description of the prompt.
- `environments` -> Optional[List[str]] *(property)* — The environments that currently own this prompt version, or ``None`` if unowned.
- `format(self, **kwargs: Any) -> Union[str, List[Dict[str, Any]]]` — Replaces placeholders in the template with provided keyword arguments.
- `from_fern_prompt_version(cls, name: str, prompt_version: opik.rest_api.types.prompt_version_detail.PromptVersionDetail, project_name: Optional[str] = None) -> 'Prompt'`
- `id` -> Optional[str] *(property)* — The unique identifier (UUID) of the prompt.
- `metadata` -> Optional[Dict[str, Any]] *(property)* — The metadata dictionary associated with the prompt
- `name` -> str *(property)* — The name of the prompt.
- `project_name` -> Optional[str] *(property)* — The name of the project this prompt belongs to.
- `prompt` -> str *(property)* — The latest template of the prompt.
- `sync_with_backend(self) -> bool` — Synchronize the prompt with the backend.
- `synced` -> bool *(property)* — Whether the prompt has been successfully synced with the backend.
- `tags` -> Optional[List[str]] *(property)* — The list of tags associated with the prompt.
- `type` -> opik.api_objects.prompt.types.PromptType *(property)* — The prompt type of the prompt.
- `version` -> Optional[str] *(property)* — The sequential version selector for the prompt (e.g. ``"v3"``).
- `version_id` -> Optional[str] *(property)* — The unique identifier of the prompt version.

## `opik.api_objects.prompt.chat.chat_prompt`

### `ChatPrompt`

> ChatPrompt class represents a chat-style prompt with a name, message array template and commit hash.

```python
ChatPrompt(name: str, messages: List[Dict[str, Union[str, List[Dict[str, Any]]]]], metadata: Optional[Dict[str, Any]] = None, type: opik.api_objects.prompt.types.PromptType = <PromptType.MUSTACHE: 'mustache'>, validate_placeholders: bool = False, id: Optional[str] = None, description: Optional[str] = None, change_description: Optional[str] = None, tags: Optional[List[str]] = None, project_name: Optional[str] = None) -> None
```

- `change_description` -> Optional[str] *(property)* — The description of changes in this version.
- `commit` -> Optional[str] *(property)* — Legacy commit hash of the prompt version.
- `description` -> Optional[str] *(property)* — The description of the prompt.
- `environments` -> Optional[List[str]] *(property)* — The environments that currently own this prompt version, or ``None`` if unowned.
- `format(self, variables: Dict[str, Any], supported_modalities: Optional[Mapping[Literal['vision', 'video'], bool]] = None) -> List[Dict[str, Union[str, List[Dict[str, Any]]]]]` — Renders the chat template with provided variables.
- `from_fern_prompt_version(cls, name: str, prompt_version: opik.rest_api.types.prompt_version_detail.PromptVersionDetail, project_name: Optional[str] = None) -> 'ChatPrompt'`
- `id` -> Optional[str] *(property)* — The unique identifier (UUID) of the prompt.
- `metadata` -> Optional[Dict[str, Any]] *(property)* — The metadata dictionary associated with the prompt
- `name` -> str *(property)* — The name of the prompt.
- `project_name` -> Optional[str] *(property)* — The name of the project this prompt belongs to.
- `sync_with_backend(self) -> bool` — Synchronize the chat prompt with the backend.
- `synced` -> bool *(property)* — Whether the chat prompt has been successfully synced with the backend.
- `tags` -> Optional[List[str]] *(property)* — The list of tags associated with the prompt.
- `template` -> List[Dict[str, Union[str, List[Dict[str, Any]]]]] *(property)* — The chat messages template.
- `type` -> opik.api_objects.prompt.types.PromptType *(property)* — The prompt type of the prompt.
- `version` -> Optional[str] *(property)* — The sequential version selector for the prompt (e.g. ``"v3"``).
- `version_id` -> Optional[str] *(property)* — The unique identifier of the prompt version.

## `opik.api_objects.prompt.types`

### `PromptType` (enum)
> str(object='') -> str
str(bytes_or_buffer[, encoding[, errors]]) -> str

- `PromptType.MUSTACHE` = `'mustache'`
- `PromptType.JINJA2` = `'jinja2'`

## `opik.api_objects.span.span_client`

### `Span`

```python
Span(id: str, trace_id: str, project_name: str, message_streamer: opik.message_processing.streamer.Streamer, url_override: str, source: Literal['sdk', 'experiment', 'optimization'], parent_span_id: Optional[str] = None, config: Optional[opik.config.OpikConfig] = None, environment: Optional[str] = None)
```

- `__init__(self, id: str, trace_id: str, project_name: str, message_streamer: opik.message_processing.streamer.Streamer, url_override: str, source: Literal['sdk', 'experiment', 'optimization'], parent_span_id: Optional[str] = None, config: Optional[opik.config.OpikConfig] = None, environment: Optional[str] = None)` — A Span object. This object should not be created directly, instead use the `span` method of a Trace (:func:`opik.Opik.span`) or another Span (:meth:`opik.Span.span`).
- `end(self, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[opik.types.LLMProvider, str, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None) -> None` — End the span and update its attributes.
- `get_distributed_trace_headers(self) -> opik.types.DistributedTraceHeadersDict` — Returns headers dictionary to be passed into tracked
function on remote node.
- `log_feedback_score(self, name: str, value: float, category_name: Optional[str] = None, reason: Optional[str] = None) -> None` — Log a feedback score for the span.
- `span(self, id: Optional[str] = None, name: Optional[str] = None, type: Literal['general', 'tool', 'llm', 'guardrail'] = 'general', start_time: Optional[datetime.datetime] = None, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: opik.types.LLMProvider = <LLMProvider.OPENAI: 'openai'>, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None) -> 'Span'` — Create a new child span within the current span.
- `update(self, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[opik.types.LLMProvider, str, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None) -> None` — Update the span attributes.

### `create_span(trace_id: str, project_name: str, url_override: str, message_streamer: opik.message_processing.streamer.Streamer, span_id: Optional[str] = None, parent_span_id: Optional[str] = None, name: Optional[str] = None, type: Literal['general', 'tool', 'llm', 'guardrail'] = 'general', start_time: Optional[datetime.datetime] = None, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[opik.types.LLMProvider, str, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None, source: Literal['sdk', 'experiment', 'optimization'] = 'sdk', config: Optional[opik.config.OpikConfig] = None, environment: Optional[str] = None) -> opik.api_objects.span.span_client.Span`

### `update_span(id: str, trace_id: str, parent_span_id: Optional[str], project_name: str, url_override: str, message_streamer: opik.message_processing.streamer.Streamer, source: Literal['sdk', 'experiment', 'optimization'], end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[opik.types.LLMProvider, str, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None, environment: Optional[str] = None) -> None`

## `opik.api_objects.trace.trace_client`

### `Trace`

```python
Trace(id: str, message_streamer: opik.message_processing.streamer.Streamer, project_name: str, url_override: str, source: Literal['sdk', 'experiment', 'optimization'], config: opik.config.OpikConfig, environment: Optional[str] = None)
```

- `__init__(self, id: str, message_streamer: opik.message_processing.streamer.Streamer, project_name: str, url_override: str, source: Literal['sdk', 'experiment', 'optimization'], config: opik.config.OpikConfig, environment: Optional[str] = None)` — A Trace object. This object should not be created directly, instead use :meth:`opik.Opik.trace` to create a new trace.
- `end(self, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[Any]] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, thread_id: Optional[str] = None) -> None` — End the trace and update its attributes.
- `log_feedback_score(self, name: str, value: float, category_name: Optional[str] = None, reason: Optional[str] = None) -> None` — Log a feedback score for the trace.
- `span(self, id: Optional[str] = None, parent_span_id: Optional[str] = None, name: Optional[str] = None, type: Literal['general', 'tool', 'llm', 'guardrail'] = 'general', start_time: Optional[datetime.datetime] = None, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[str]] = None, usage: Union[Dict[str, Any], opik.llm_usage.opik_usage.OpikUsage, NoneType] = None, model: Optional[str] = None, provider: Union[opik.types.LLMProvider, str, NoneType] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, total_cost: Optional[float] = None, attachments: Optional[List[opik.api_objects.attachment.attachment.Attachment]] = None) -> opik.api_objects.span.span_client.Span` — Create a new span within the trace.
- `update(self, end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[Any]] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, thread_id: Optional[str] = None) -> None` — Update the trace attributes.

### `update_trace(trace_id: str, project_name: str, message_streamer: opik.message_processing.streamer.Streamer, source: Literal['sdk', 'experiment', 'optimization'], end_time: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None, input: Optional[Dict[str, Any]] = None, output: Optional[Dict[str, Any]] = None, tags: Optional[List[Any]] = None, error_info: Optional[opik.types.ErrorInfoDict] = None, thread_id: Optional[str] = None, environment: Optional[str] = None) -> None`
> Update an existing trace with new information.
> This function sends an UpdateTraceMessage to the provided message_streamer,
> allowing you to update various fields of a trace, such as its end time,
> metadata, input, output, tags, error information and thread association.
>
> Args:
>     trace_id: The unique identifier of the trace to update.
>     project_name: The name of the project associated with the trace.
>     message_streamer: The message streamer used to send the update.
>     end_time: The end time of the trace. Defaults to None.
>     metadata: Additional metadata for the trace. Defaults to None.
>     input: Input data associated with the trace. Defaults to None.
>     output: Output data associated with the trace. Defaults to None.
>     tags: List of tags to associate with the trace. Defaults to None.
>     error_info: Error information related to the trace. Defaults to None.
>     thread_id : The thread ID associated with the trace. Defaults to None.
>     source: The source of the update. This can be either "sdk", "experiment", "optimization".
> Returns:
>     None
> Usage Notes:
>     - This function does not return a value; it sends an update message to the message streamer.
>     - All parameters except trace_id, project_name and message_streamer are optional.
>     - Only the fields provided will be updated in the trace.

## `opik.api_objects.threads.threads_client`

### `ThreadsClient`

> Client for managing and interacting with conversational threads.

```python
ThreadsClient(client: 'opik.Opik')
```

- `__init__(self, client: 'opik.Opik')` — Initialize self.  See help(type(self)) for accurate signature.
- `client` -> 'opik.Opik' *(property)*
- `log_threads_feedback_scores(self, scores: List[opik.types.BatchFeedbackScoreDict], project_name: Optional[str] = None) -> None` — Logs feedback scores for threads in a specific project. This method processes the given
feedback scores and associates them with the specified project if a project name is
provided. It is designed to handle multiple scores in a structured manner.
- `opik_client` -> 'opik.Opik' *(property)*
- `search_threads(self, project_name: Optional[str] = None, filter_string: Optional[str] = None, max_results: int = 1000, truncate: bool = True) -> List[opik.rest_api.types.trace_thread.TraceThread]` — Search for threads in a given project based on specific criteria.

## `opik.api_objects.annotation_queue.annotation_queue`

### `BaseAnnotationQueue`

> Base class for annotation queue objects.

```python
BaseAnnotationQueue(id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None
```

- `__init__(self, id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `comments_enabled` -> Optional[bool] *(property)* — Whether comments are enabled for this queue.
- `delete(self) -> None` — Delete this annotation queue.
- `description` -> Optional[str] *(property)* — The description of the annotation queue.
- `feedback_definition_names` -> Optional[List[str]] *(property)* — The feedback definition names associated with this queue.
- `id` -> str *(property)* — The id of the annotation queue.
- `instructions` -> Optional[str] *(property)* — The instructions for reviewers.
- `items_count` -> Optional[int] *(property)* — The total number of items in the queue.
- `name` -> str *(property)* — The name of the annotation queue.
- `project_id` -> str *(property)* — The project ID associated with this annotation queue.
- `scope` -> str *(property)* — The scope of the annotation queue ('trace' or 'thread').
- `update(self, name: Optional[str] = None, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None) -> None` — Update the annotation queue properties.

### `ThreadsAnnotationQueue`

> An annotation queue for threads.

```python
ThreadsAnnotationQueue(id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None
```

- `__init__(self, id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `add_threads(self, threads: List[opik.rest_api.types.trace_thread.TraceThread]) -> None` — Add thread objects to the annotation queue.
- `comments_enabled` -> Optional[bool] *(property)* — Whether comments are enabled for this queue.
- `delete(self) -> None` — Delete this annotation queue.
- `description` -> Optional[str] *(property)* — The description of the annotation queue.
- `feedback_definition_names` -> Optional[List[str]] *(property)* — The feedback definition names associated with this queue.
- `get_items(self, truncate_images: bool = True) -> List[opik.rest_api.types.trace_thread.TraceThread]` — Get all thread objects currently in the annotation queue.
- `id` -> str *(property)* — The id of the annotation queue.
- `instructions` -> Optional[str] *(property)* — The instructions for reviewers.
- `items_count` -> Optional[int] *(property)* — The total number of items in the queue.
- `name` -> str *(property)* — The name of the annotation queue.
- `project_id` -> str *(property)* — The project ID associated with this annotation queue.
- `remove_threads(self, threads: List[opik.rest_api.types.trace_thread.TraceThread]) -> None` — Remove thread objects from the annotation queue.
- `scope` -> str *(property)* — The scope of the annotation queue.
- `update(self, name: Optional[str] = None, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None) -> None` — Update the annotation queue properties.

### `TracesAnnotationQueue`

> An annotation queue for traces.

```python
TracesAnnotationQueue(id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None
```

- `__init__(self, id: str, name: str, project_id: str, rest_client: opik.rest_api.client.OpikApi, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None, items_count: Optional[int] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `add_traces(self, traces: List[Union[opik.api_objects.trace.trace_client.Trace, opik.rest_api.types.trace_public.TracePublic]]) -> None` — Add trace objects to the annotation queue.
- `comments_enabled` -> Optional[bool] *(property)* — Whether comments are enabled for this queue.
- `delete(self) -> None` — Delete this annotation queue.
- `description` -> Optional[str] *(property)* — The description of the annotation queue.
- `feedback_definition_names` -> Optional[List[str]] *(property)* — The feedback definition names associated with this queue.
- `get_items(self, truncate_images: bool = True) -> List[opik.rest_api.types.trace_public.TracePublic]` — Get all trace objects currently in the annotation queue.
- `id` -> str *(property)* — The id of the annotation queue.
- `instructions` -> Optional[str] *(property)* — The instructions for reviewers.
- `items_count` -> Optional[int] *(property)* — The total number of items in the queue.
- `name` -> str *(property)* — The name of the annotation queue.
- `project_id` -> str *(property)* — The project ID associated with this annotation queue.
- `remove_traces(self, traces: List[Union[opik.api_objects.trace.trace_client.Trace, opik.rest_api.types.trace_public.TracePublic]]) -> None` — Remove trace objects from the annotation queue.
- `scope` -> str *(property)* — The scope of the annotation queue.
- `update(self, name: Optional[str] = None, description: Optional[str] = None, instructions: Optional[str] = None, comments_enabled: Optional[bool] = None, feedback_definition_names: Optional[List[str]] = None) -> None` — Update the annotation queue properties.

## `opik.api_objects.attachment.attachment`

### `Attachment`

> Represents an Attachment to be added to the Trace or Span.

**Fields:**
- `data: Union[str, bytes]` *(required)*
- `file_name: Optional[str] = None`
- `content_type: Optional[str] = None`
- `create_temp_copy: bool = True`


## `opik.api_objects.feedback_score.converters`

### `feedback_scores_public_to_feedback_scores_dict(feedback_scores_public: List[opik.rest_api.types.feedback_score_public.FeedbackScorePublic]) -> List[opik.types.FeedbackScoreDict]`

## `opik.api_objects.conversation.conversation_thread`

### `ConversationThread`

> Represents a conversation thread composed of multiple conversation items.

**Fields:**
- `discussion: List[opik.api_objects.conversation.conversation_thread.ConversationThreadItem]` *(required)*

- `add_assistant_message(self, message: str) -> None`
- `add_item(self, item: opik.api_objects.conversation.conversation_thread.ConversationThreadItem) -> None`
- `add_system_message(self, message: str) -> None`
- `add_user_message(self, message: str) -> None`
- `as_json_list(self) -> List[Dict[str, str]]`

### `ConversationThreadItem`

> Represents a single message within a conversation thread.

**Fields:**
- `role: str` *(required)*
- `content: str` *(required)*


## `opik.api_objects.agent_config.base`

### `Config`

> Base class for user-defined configurations.

```python
Config() -> None
```

- `__init__(self) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `is_fallback` -> bool *(property)* — True if local fallback values are used because there was an issue communicating with the backend.

## `opik.api_objects.agent_config.config`

### `ConfigManager`

> Project-level config entity — internal REST operations.

```python
ConfigManager(project_name: str, rest_client_: opik.rest_api.client.OpikApi) -> None
```

- `__init__(self, project_name: str, rest_client_: opik.rest_api.client.OpikApi) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `create_blueprint(self, parameters: Optional[Dict[str, Any]] = None, fields_with_values: Optional[Dict[str, opik.api_objects.agent_config.types.FieldValueSpec]] = None, description: Optional[str] = None, field_types: Optional[Dict[str, Any]] = None) -> opik.api_objects.agent_config.blueprint.Blueprint` — Create and return the initial blueprint for this agent config (first version only).
- `create_mask(self, parameters: Optional[Dict[str, Any]] = None, fields_with_values: Optional[Dict[str, opik.api_objects.agent_config.types.FieldValueSpec]] = None, description: Optional[str] = None) -> str` — Create a mask blueprint and return its ID.
- `get_blueprint(self, *, name: Optional[str] = None, env: Optional[str] = None, mask_id: Optional[str] = None, field_types: Optional[Dict[str, Any]] = None, timeout_in_seconds: Optional[int] = None) -> Optional[opik.api_objects.agent_config.blueprint.Blueprint]` — Fetch a blueprint by name, environment name, or latest.
- `project_name` -> str *(property)*
- `set_env(self, version: str, env: str) -> None` — Tag a specific blueprint version with an environment name.
- `update_blueprint(self, fields_with_values: Optional[Dict[str, opik.api_objects.agent_config.types.FieldValueSpec]] = None, description: Optional[str] = None, field_types: Optional[Dict[str, Any]] = None) -> opik.api_objects.agent_config.blueprint.Blueprint` — Create a new blueprint with only the supplied fields (not merged with previous).

## `opik.api_objects.agent_config.types`

### `FieldValueSpec`

> Describes a single blueprint field's value for write operations.

```python
FieldValueSpec(python_type: type[typing.Any], value: Any)
```


## `opik.api_objects.optimization.optimization`

### `Optimization`

```python
Optimization(id: str, rest_client: opik.rest_api.client.OpikApi, project_name: Optional[str] = None) -> None
```

- `__init__(self, id: str, rest_client: opik.rest_api.client.OpikApi, project_name: Optional[str] = None) -> None` — Initialize self.  See help(type(self)) for accurate signature.
- `fetch_content(self) -> opik.rest_api.types.optimization_public.OptimizationPublic`
- `id` -> str *(property)*
- `project_name` -> Optional[str] *(property)*
- `update(self, name: Optional[str] = None, status: Optional[Literal['running', 'completed', 'cancelled', 'initialized', 'error']] = None) -> None`

## `opik.types`

### `BatchAssertionResultDict`

> A TypedDict representing an assertion result for batch operations.

**Fields:**
- `id: str` *(required)*
- `name: str` *(required)*
- `status: Literal['passed', 'failed']` *(required)*
- `project_name: Optional[Annotated[str, Strict(strict=True)]]`
- `reason: Optional[str]`


### `BatchFeedbackScoreDict`

> A TypedDict representing a feedback score for batch operations.

**Fields:**
- `id: str` *(required)*
- `name: str` *(required)*
- `value: float` *(required)*
- `project_name: Optional[Annotated[str, Strict(strict=True)]]`
- `category_name: Optional[str]`
- `reason: Optional[str]`


### `DistributedTraceHeadersDict`

> Contains headers for distributed tracing, returned by the :py:func:`opik.opik_context.get_distributed_trace_headers` function.

**Fields:**
- `opik_trace_id: str` *(required)*
- `opik_parent_span_id: str` *(required)*


### `ErrorInfoDict`

> A TypedDict representing the information about the error occurred.

**Fields:**
- `exception_type: str` *(required)*
- `message: str`
- `traceback: str` *(required)*


### `FeedbackScoreDict`

> A TypedDict representing a feedback score.

**Fields:**
- `id: str`
- `name: str` *(required)*
- `value: float` *(required)*
- `category_name: Optional[str]`
- `reason: Optional[str]`


### `LLMProvider` (enum)
> str(object='') -> str
str(bytes_or_buffer[, encoding[, errors]]) -> str

- `LLMProvider.GOOGLE_VERTEXAI` = `'google_vertexai'`
- `LLMProvider.GOOGLE_AI` = `'google_ai'`
- `LLMProvider.OPENAI` = `'openai'`
- `LLMProvider.ANTHROPIC` = `'anthropic'`
- `LLMProvider.ANTHROPIC_VERTEXAI` = `'anthropic_vertexai'`
- `LLMProvider.GROQ` = `'groq'`
- `LLMProvider.BEDROCK` = `'bedrock'`
