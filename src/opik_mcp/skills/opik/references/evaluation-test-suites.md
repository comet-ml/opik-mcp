---
last_updated: "2026-04-17"
source_commit: "2.0.0"
---

# Test Suites: Evaluation & Regression Testing

Test Suites are the **recommended way** to evaluate agents in Opik. They combine test items with LLM-judge assertions and execution policies for reliable, reproducible testing.

**Prefer Test Suites over the legacy Dataset + `evaluate()` approach.** Only use `evaluate()` if the user is already on that path or explicitly requests custom metric-based evaluation (see `references/evaluation-datasets.md`).

## Core Concepts

A **Test Suite** stores:
- **Items** with input data and optional per-item assertions
- **Global assertions** (string descriptions checked by an LLM judge, applied to all items)
- **Execution policies** controlling multi-run reliability (`runs_per_item` / `runsPerItem`, `pass_threshold` / `passThreshold`)
- **Versions** (automatic, immutable snapshots on every change)

An **evaluation run** via `run_tests()` / `runTests()` processes each item through the user's agent, checks assertions with an LLM judge, and returns a `TestSuiteResult` with pass/fail per item.

## Creating & Managing Test Suites

### Create or Fetch

**Python:**

```python
import opik

client = opik.Opik()

# Create with global assertions and execution policy
suite = client.get_or_create_test_suite(
    name="my-agent-suite",
    global_assertions=[
        "Response is factually accurate and not hallucinated",
        "Response is professional in tone",
    ],
    global_execution_policy={"runs_per_item": 3, "pass_threshold": 2},
    tags=["regression", "v2"],
    project_name="my-project",
)

# Fetch existing
suite = client.get_test_suite(name="my-agent-suite", project_name="my-project")

# List all suites
suites = client.get_test_suites(max_results=100, project_name="my-project")
```

**TypeScript:**

```typescript
import { Opik } from "opik";

const client = new Opik();

// Create with global assertions and execution policy
const suite = await client.getOrCreateTestSuite({
  name: "my-agent-suite",
  globalAssertions: [
    "Response is factually accurate and not hallucinated",
    "Response is professional in tone",
  ],
  globalExecutionPolicy: { runsPerItem: 3, passThreshold: 2 },
  tags: ["regression", "v2"],
  projectName: "my-project",
});

// Fetch existing
const suite = await client.getTestSuite("my-agent-suite", "my-project");

// List all suites
const suites = await client.getTestSuites(100, "my-project");
```

### Other Client Methods

**Python:**

```python
# Create (fails if already exists)
suite = client.create_test_suite(name="new-suite", ...)

# Delete
client.delete_test_suite(name="my-agent-suite", project_name="my-project")

# Get experiments for a suite
experiments = client.get_test_suite_experiments(name="my-agent-suite", project_name="my-project")
```

**TypeScript:**

```typescript
// Create (fails if already exists)
const suite = await client.createTestSuite({ name: "new-suite" });

// Delete
await client.deleteTestSuite("my-agent-suite", "my-project");
```

## Managing Items

Items are plain dicts with a required `data` field (typed as `TestSuiteItem` TypedDict in Python, `TestSuiteItem` interface in TypeScript):

**Python:**

```python
# Insert items (item-level assertions are optional and add to global ones)
suite.insert([
    {
        "data": {"input": "What is the capital of France?"},
        "assertions": ["Response correctly identifies Paris as the capital"],
        "description": "Basic geography question",
    },
    {
        "data": {"input": "Explain quantum computing simply"},
        # No item-level assertions — global assertions still apply
    },
])

# Update existing items (must include 'id')
items = suite.get_items()
suite.update([
    {
        "id": items[0]["id"],
        "data": items[0]["data"],
        "assertions": ["Updated assertion for this item"],
    },
])

# Delete specific items
suite.delete(items_ids=[items[0]["id"]])

# Clear all items
suite.clear()

# Retrieve items (with optional filtering)
all_items = suite.get_items()
sample = suite.get_items(nb_samples=10)
filtered = suite.get_items(filter_string='data.input contains "quantum"')
```

**TypeScript:**

```typescript
import type { TestSuiteItem } from "opik";

// Insert items
await suite.insert([
  {
    data: { input: "What is the capital of France?" },
    assertions: ["Response correctly identifies Paris as the capital"],
    description: "Basic geography question",
  },
  {
    data: { input: "Explain quantum computing simply" },
  },
]);

// Update existing items (must include 'id')
const items = await suite.getItems();
await suite.update([
  {
    id: items[0].id,
    data: items[0].data,
    assertions: ["Updated assertion for this item"],
  },
]);

// Update individual item assertions or execution policy (TS only)
await suite.updateItemAssertions(items[0].id, ["New assertion"]);
await suite.updateItemExecutionPolicy(items[0].id, { runsPerItem: 5, passThreshold: 5 });
await suite.updateItem(items[0].id, {
  assertions: ["New assertion"],
  executionPolicy: { runsPerItem: 3, passThreshold: 2 },
});

// Delete specific items
await suite.delete([items[0].id]);

// Clear all items
await suite.clear();

// Retrieve items
const allItems = await suite.getItems();
```

**Note:** `get_items()` in Python supports `nb_samples` and `filter_string` parameters. The TypeScript `getItems()` returns all items — filtering is not yet supported.

### Item-Level Execution Policies

Override the suite-level policy for high-stakes items:

**Python:**

```python
suite.insert([
    {
        "data": {"input": "Calculate the dosage for a 70kg patient"},
        "assertions": ["Dosage calculation is mathematically correct"],
        "execution_policy": {"runs_per_item": 5, "pass_threshold": 5},  # Must pass all 5
    },
])
```

**TypeScript:**

```typescript
await suite.insert([
  {
    data: { input: "Calculate the dosage for a 70kg patient" },
    assertions: ["Dosage calculation is mathematically correct"],
    executionPolicy: { runsPerItem: 5, passThreshold: 5 },
  },
]);
```

## Global Settings

**Python:**

```python
# Read current settings
assertions = suite.get_global_assertions()
policy = suite.get_global_execution_policy()
tags = suite.get_tags()

# Update (creates a new version)
suite.update_test_settings(
    global_assertions=[
        "Response is factually accurate",
        "Response is safe and appropriate",
        "Response addresses the user's question directly",
    ],
    global_execution_policy={"runs_per_item": 3, "pass_threshold": 2},
)
```

**TypeScript:**

```typescript
// Read current settings
const assertions = await suite.getGlobalAssertions();
const policy = await suite.getGlobalExecutionPolicy();
const tags = await suite.getTags();

// Update (creates a new version)
await suite.updateTestSettings({
  globalAssertions: [
    "Response is factually accurate",
    "Response is safe and appropriate",
    "Response addresses the user's question directly",
  ],
  globalExecutionPolicy: { runsPerItem: 3, passThreshold: 2 },
});
```

## Versioning

Opik automatically creates immutable versions when items or settings change.

**Python:**

```python
# Current version
version_name = suite.get_current_version_name()  # e.g. "v3"
version_info = suite.get_version_info()
print(f"Version {version_info.version_name}: {version_info.items_total} items")
print(f"  Added: {version_info.items_added}, Modified: {version_info.items_modified}")
print(f"  Created: {version_info.created_at} by {version_info.created_by}")

# Get a read-only snapshot of a specific version
v1 = suite.get_version_view("v1")
v1_items = v1.get_items()
v1_assertions = v1.get_global_assertions()
v1_policy = v1.get_global_execution_policy()
```

**TypeScript:**

```typescript
// Current version
const versionName = await suite.getCurrentVersionName(); // e.g. "v3"
const versionInfo = await suite.getVersionInfo();

// Get a read-only snapshot of a specific version
const v1 = await suite.getVersionView("v1");
```

`TestSuiteVersion` properties (Python): `name`, `version_name`, `version_id`, `version_hash`, `is_latest`, `items_total`, `items_added`, `items_modified`, `items_deleted`, `change_description`, `created_at`, `created_by`, `tags`, `project_name`.

## Running Tests

### Basic Usage

**Python:**

```python
import opik

client = opik.Opik()
suite = client.get_test_suite(name="my-agent-suite")

results = opik.run_tests(
    test_suite=suite,
    task=lambda item: {"output": my_agent(item["input"])},
)

# CI gate
assert results.all_items_passed
```

**TypeScript:**

```typescript
import { Opik, runTests } from "opik";

const client = new Opik();
const suite = await client.getTestSuite("my-agent-suite");

const results = await runTests({
  testSuite: suite,
  task: async (item) => ({
    input: item.input,
    output: await myAgent(item.input),
  }),
});

// CI gate
if (!results.allItemsPassed) {
  process.exit(1);
}
```

### Full Parameters

**Python:**

```python
results = opik.run_tests(
    test_suite=suite,                          # or a TestSuiteVersion for pinned runs
    task=my_evaluation_task,
    experiment_name="regression-v2.1",         # auto-generated if omitted
    experiment_name_prefix="nightly-",         # prefix for auto-generated names
    experiment_config={"model": "gpt-4o", "temperature": 0.1},
    prompts=[my_prompt],                       # link prompt versions to experiment
    experiment_tags=["nightly", "v2.1"],
    model="gpt-4o",                            # model for assertion checking (LLM judge)
    blueprint_id="agent-config-uuid",          # link experiment to an Agent Configuration version
    verbose=2,                                 # 0=silent, 1=summary, 2=detailed
    worker_threads=16,
    generate_report=True,
    report_output_path="./test-report.json",
)
```

Pass `blueprint_id` only when the user is versioning their agent via Opik Agent Configurations — the experiment's Configuration tab then shows a clickable link back to the exact agent config version used.

**TypeScript:**

```typescript
const results = await runTests({
  testSuite: suite,
  task: myEvaluationTask,
  experimentName: "regression-v2.1",
  projectName: "my-project",
  experimentConfig: { model: "gpt-4o", temperature: 0.1 },
  prompts: [myPrompt],
  experimentTags: ["nightly", "v2.1"],
  model: "gpt-4o",                            // model for assertion checking (LLM judge)
  blueprintId: "agent-config-uuid",           // link experiment to an Agent Configuration version
});
```

**Note:** Python has `verbose`, `worker_threads`, `generate_report`, `report_output_path`, and `experiment_name_prefix` parameters that are not available in TypeScript. TypeScript has `projectName` which is not available in Python's `run_tests()` — in Python, the project is inherited from the test suite itself.

### Task Function

The task receives each item's `data` dict and returns a dict with `input`/`output` keys:

**Python:**

```python
def my_evaluation_task(item: dict) -> dict:
    query = item["input"]
    response = my_agent(query)
    return {
        "input": query,
        "output": response,
    }
```

**TypeScript:**

```typescript
async function myEvaluationTask(item: Record<string, unknown>) {
  const query = item.input as string;
  const response = await myAgent(query);
  return {
    input: query,
    output: response,
  };
}
```

### Pinning to a Version

```python
# Run against a specific version for reproducibility
v1 = suite.get_version_view("v1")
results = opik.run_tests(test_suite=v1, task=my_task)
```

## Test Results

`run_tests()` / `runTests()` returns a `TestSuiteResult`:

**Python:**

```python
results = opik.run_tests(test_suite=suite, task=my_task)

# Summary
print(f"Passed: {results.items_passed}/{results.items_total}")
print(f"Pass rate: {results.pass_rate:.1%}")  # Excludes items without assertions
print(f"Total time: {results.total_time:.1f}s")
print(f"Suite: {results.suite_name}")
print(f"Experiment: {results.experiment_name} ({results.experiment_url})")

# CI gate
assert results.all_items_passed, f"Failed: {results.items_total - results.items_passed} items"

# Per-item details
for item_id, item_result in results.item_results.items():
    print(f"  Item {item_id}: {'PASS' if item_result.passed else 'FAIL'}")
    print(f"    Runs: {item_result.runs_passed}/{item_result.runs_total}")
    print(f"    Threshold: {item_result.pass_threshold}/{item_result.configured_runs_per_item}")
    if not item_result.passed:
        for run in item_result.test_results:
            print(f"      Run scores: {run.score_results}")

# Export as dict (for CI artifacts, dashboards)
report = results.to_dict()
```

**TypeScript:**

```typescript
const results = await runTests({ testSuite: suite, task: myTask });

// Summary
console.log(`Passed: ${results.itemsPassed}/${results.itemsTotal}`);
console.log(`Pass rate: ${results.passRate}`);
console.log(`Experiment: ${results.experimentName} (${results.experimentUrl})`);

// CI gate
if (!results.allItemsPassed) {
  process.exit(1);
}

// Per-item details
for (const [itemId, itemResult] of results.itemResults) {
  console.log(`  Item ${itemId}: ${itemResult.passed ? "PASS" : "FAIL"}`);
  console.log(`    Runs: ${itemResult.runsPassed}/${itemResult.runsTotal}`);
}
```

### ItemResult Fields

Each `ItemResult` contains:

| Field | Python | TypeScript |
|-------|--------|-----------|
| Overall pass/fail | `passed` | `passed` |
| Has assertions | `has_assertions` | `hasAssertions` |
| Runs passed / total | `runs_passed` / `runs_total` | `runsPassed` / `runsTotal` |
| Policy: runs per item | `configured_runs_per_item` | `configuredRunsPerItem` |
| Policy: threshold | `pass_threshold` | `passThreshold` |
| Individual run results | `test_results` | `testResults` |

### TestSuiteResult Fields

| Field | Python | TypeScript |
|-------|--------|-----------|
| All items passed | `all_items_passed` | `allItemsPassed` |
| Items passed / total | `items_passed` / `items_total` | `itemsPassed` / `itemsTotal` |
| Pass rate | `pass_rate` | `passRate` |
| Total time (seconds) | `total_time` | `totalTime` |
| Suite name | `suite_name` | `suiteName` |
| Experiment name | `experiment_name` | `experimentName` |
| Experiment URL | `experiment_url` | `experimentUrl` |
| Experiment ID | `experiment_id` | `experimentId` |
| Item results map | `item_results` | `itemResults` |
| Export to dict | `to_dict()` | `toDict()` / `toReportDict()` |

## CI/CD Integration

**Python:**

```python
import sys
import opik

client = opik.Opik()
suite = client.get_test_suite(name="regression-suite")

results = opik.run_tests(
    test_suite=suite,
    task=lambda item: {"output": my_agent(item["input"])},
    experiment_tags=["ci", os.environ.get("CI_COMMIT_SHA", "local")],
    generate_report=True,
    report_output_path="./test-report.json",
)

if not results.all_items_passed:
    print(f"FAILED: {results.items_total - results.items_passed}/{results.items_total} items failed")
    sys.exit(1)

print(f"All {results.items_total} items passed ({results.pass_rate:.0%} pass rate)")
```

**TypeScript:**

```typescript
import { Opik, runTests } from "opik";

const client = new Opik();
const suite = await client.getTestSuite("regression-suite");

const results = await runTests({
  testSuite: suite,
  task: async (item) => ({
    input: item.input,
    output: await myAgent(item.input as string),
  }),
  experimentTags: ["ci", process.env.CI_COMMIT_SHA ?? "local"],
});

if (!results.allItemsPassed) {
  console.error(
    `FAILED: ${results.itemsTotal - results.itemsPassed}/${results.itemsTotal} items failed`
  );
  process.exit(1);
}

console.log(`All ${results.itemsTotal} items passed`);
```

## SDK Differences

| Feature | Python | TypeScript |
|---------|--------|-----------|
| Item filtering | `get_items(nb_samples, filter_string)` | `getItems()` (no filtering) |
| Per-item update methods | Use `update()` with full item | `updateItemAssertions()`, `updateItemExecutionPolicy()`, `updateItem()` |
| `project_name` in run | *(not available — inherited from suite)* | `projectName` in `runTests()` |
| Suite experiments | `client.get_test_suite_experiments()` | *(not available)* |
| Report generation | `generate_report`, `report_output_path` | *(not available)* |
| Verbosity control | `verbose=0/1/2` | *(not available)* |
| Worker threads | `worker_threads` | *(not available)* |
| Version pinning | `run_tests(test_suite=version)` | *(not available)* |

## Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| All items pass even with wrong outputs | No assertions defined | Add global assertions or per-item assertions |
| Flaky results across runs | Non-deterministic agent + strict policy | Increase `runs_per_item` and tune `pass_threshold` |
| Assertion checking is slow | Too many items or runs | Reduce `runs_per_item`, use a faster `model` for the judge |
| `TestSuiteResult.pass_rate` is `None` / `undefined` | No items have assertions | Add assertions to the suite or items |
| Version not found | Typo in version name | Use `get_current_version_name()` / `getCurrentVersionName()` to check available versions |
