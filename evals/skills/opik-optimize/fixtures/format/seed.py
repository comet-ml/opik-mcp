# mypy: ignore-errors
"""Seed a dataset + a deliberately weak library prompt for the /opik-optimize evals.

The task is chosen so the PROMPT is the bottleneck: each item is an order line
("Order 1042: 3 widgets at 12.50 each plus 4.00 shipping") and the expected output
is a strict format the weak prompt never mentions ("TOTAL: 41.50"). A model can do
the arithmetic; what it lacks is the format instruction. MetaPromptOptimizer finds
it within a few trials, so a real gain is reliably available — and a skill that
reports the training score instead of validation, or saves a version on a flat
result, is caught by the grader.

Writes planted.json = {project, dataset, dataset_id, prompt, prompt_version, items}.
Set OPIK_OPTIMIZE_EVAL_ITEMS to change the size (default 50).
"""

import json
import os
import random
import uuid
from pathlib import Path

TAG = uuid.uuid4().hex[:8]
PROJECT = os.environ.get("OPIK_OPTIMIZE_EVAL_PROJECT") or f"opik-optimize-eval-{TAG}"
os.environ["OPIK_PROJECT_NAME"] = PROJECT
DATASET = f"{PROJECT}-orders"
PROMPT = f"{PROJECT}-prompt"
N = int(os.environ.get("OPIK_OPTIMIZE_EVAL_ITEMS", "50"))
WEAK_PROMPT = "You are a helpful assistant for an online store. Help the customer with their order."

import opik  # noqa: E402

ITEMS_POOL = ["widgets", "gadgets", "cables", "mugs", "notebooks", "lamps", "chargers", "posters"]


def make_item(rng: random.Random, i: int) -> dict:
    qty = rng.randint(1, 6)
    price = rng.choice([4.99, 7.5, 12.5, 19.99, 25.0, 33.25])
    ship = rng.choice([0.0, 4.0, 5.99, 9.5])
    name = rng.choice(ITEMS_POOL)
    total = round(qty * price + ship, 2)
    ship_txt = f" plus {ship:.2f} shipping" if ship else " with free shipping"
    return {
        "input": f"Order {1000 + i}: {qty} {name} at {price:.2f} each{ship_txt}. What do I owe?",
        "expected_output": f"TOTAL: {total:.2f}",
    }


def main() -> None:
    rng = random.Random(42)
    client = opik.Opik(project_name=PROJECT)
    ds = client.get_or_create_dataset(name=DATASET, project_name=PROJECT)
    ds.insert([make_item(rng, i) for i in range(N)])
    prompt = client.create_prompt(name=PROMPT, prompt=WEAK_PROMPT, project_name=PROJECT)
    out = {
        "project": PROJECT,
        "dataset": DATASET,
        "dataset_id": ds.id,
        "prompt": PROMPT,
        "prompt_version": prompt.version,
        "items": len(ds.get_items()),
    }
    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps(out))


if __name__ == "__main__":
    main()
