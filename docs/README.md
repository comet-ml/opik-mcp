# opik-mcp docs

Product and engineering specs only. Plans and working notes stay local.

One design doc per feature. Pick the doc by the question you hold; the last
column says which paths it owns, so a boundary question is settled here.

| Feature | Answers | Owns |
|---|---|---|
| [tool-surface](tool-surface/design-doc.md) | How `read`, `list` and `schema` behave, what a host receives on `initialize`, and the wire contract the conformance suite pins. | `src/opik_mcp/read_list/` root modules, `entities/trace.py`, `span.py`, `thread.py`, `prompt.py`, the read, list and schema registrations in `src/opik_mcp/server.py`, `src/opik_mcp/instructions.py`, `tests/test_read_list/`, `tests/conformance/`, `tests/e2e/` shared files |
| [writes](writes/design-doc.md) | How `write(operation, data)` goes from arguments to a backend call and back, and what an error looks like. | `src/opik_mcp/writes/` except the evaluation and diagnostics operation hooks, the write registration in `src/opik_mcp/server.py`, `scripts/smoke_mcp_session.py`, `scripts/smoke_wire_capture.py`, `scripts/smoke_live_be.py`, `tests/test_writes/`, `tests/integration/` |
| [experiment-flows](experiment-flows/design-doc.md) | Experiments, datasets and cases: the comparison table, finding a case, the evaluation writes. | `src/opik_mcp/read_list/entities/experiment.py`, `src/opik_mcp/read_list/entities/dataset/`, `src/opik_mcp/read_list/sample.py`, `src/opik_mcp/writes/operations/evaluation.py`, their tests |
| [project-overview](project-overview/design-doc.md) | `read('project')` and `list('project_metric')`: the summary, the vocabulary, the series. | `src/opik_mcp/read_list/entities/project/`, `src/opik_mcp/read_list/entities/project_metric/`, `src/opik_mcp/read_list/entities/score_name.py`, `src/opik_mcp/read_list/entities/online_rule.py`, their tests |
| [diagnostics](diagnostics/design-doc.md) | Agent Insights issues and jobs: listing, reading, closing, enabling. | `src/opik_mcp/read_list/entities/agent_insights_issue/`, `src/opik_mcp/writes/operations/diagnostics.py`, `src/opik_mcp/read_list/project_scope.py`, their tests |
| [skills](skills/design-doc.md) | The skills, `read_skill`, and the pack published as `comet-ml/opik-skills`. | `src/opik_mcp/skills/`, `src/opik_mcp/skills_catalog.py`, `src/opik_mcp/skills_resources.py`, the read_skill registration in `src/opik_mcp/server.py`, `scripts/build_skills_pack.py`, `scripts/skills_trigger_eval.py`, `scripts/smoke_skills_mcp.py`, the pack jobs in `.github/workflows/ci.yaml`, `.claude-plugin/`, the skills tests |
| [hosted-auth](hosted-auth/design-doc.md) | Bearer tokens, API keys, identity and the HTTP app the Docker image serves. | The middleware and `build_app` in `src/opik_mcp/server.py`, `src/opik_mcp/auth_context.py`, `src/opik_mcp/oauth_identity.py`, `src/opik_mcp/account_identity.py`, `src/opik_mcp/caller_identity.py`, `src/opik_mcp/credential_identity.py`, the OAuth and HTTP fields of `src/opik_mcp/config.py`, the oauth, http, health and identity tests |
| [analytics](analytics/design-doc.md) | Product events, error kinds and error tracking. | `src/opik_mcp/analytics/`, `src/opik_mcp/error_kinds.py`, `src/opik_mcp/error_tracking.py`, the props functions in `src/opik_mcp/server.py`, `scripts/capture_bi_listener.py`, the analytics tests |
| [release](release/design-doc.md) | From a merge to `main` to PyPI, the image and the chart. | `.github/workflows/`, `Dockerfile`, `helm/`, the version and docker targets in `Makefile`, `version.txt`, `src/opik_mcp/_version.py`, `tests/e2e/test_wheel_contents.py`, `tests/test_install_branch.py` |
| [runtime](runtime/design-doc.md) | How the process starts, is configured and talks to Opik. | `src/opik_mcp/__main__.py`, `src/opik_mcp/config.py` outside the OAuth and HTTP fields, `src/opik_mcp/opik_client.py`, the lifespan in `src/opik_mcp/server.py`, `tests/test_opik_client.py`, `tests/test_opik_client_read.py`, `tests/test_opik_client_search.py`, `tests/test_config.py`, `tests/test_connection_per_tool_call.py` |

- [decisions/](decisions/README.md): architecture decisions (ADRs).

A PR that changes a feature's behaviour updates its design doc. These files are
public: no customer or workspace names, internal links or secrets.
