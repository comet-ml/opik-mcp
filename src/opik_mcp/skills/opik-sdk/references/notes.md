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
