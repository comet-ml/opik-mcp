---
last_updated: "2026-09-15"
source_commit: "2.0.0"
---

# SDK snippets

## Resolve the prompt

```python
from opik_optimizer import ChatPrompt

prompt = ChatPrompt(
    name="<name>", system="<system text>", user="{question}"
)  # or messages=[...]; {var} names must match dataset keys
```

## Resolve the dataset

```python
import opik

client = opik.Opik()
dataset = client.get_dataset(name="<dataset>", project_name="<project>")
```

## Run the optimizer

```python
from opik_optimizer import MetaPromptOptimizer

optimizer = MetaPromptOptimizer(model="<task model>", verbose=1, seed=42)
result = optimizer.optimize_prompt(
    prompt=prompt,
    dataset=train,
    metric=metric,
    validation_dataset=validation,
    n_samples=50,
    max_trials=10,
    project_name="<project>",
)
```

## Save the winner

```python
messages = result.prompt.get_messages()  # optimizer ChatPrompt -> raw messages
new_version = client.create_chat_prompt(
    name="<name>",
    messages=messages,
    project_name="<project>",
    change_description=f"opik-optimize: {result.optimizer}, {result.metric_name} {result.initial_score:.2f} -> {result.score:.2f} (validation)",
)
```
