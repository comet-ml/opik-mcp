# mypy: ignore-errors
"""Seed a fresh Opik project with production-shaped traces for the /opik-online-eval evals.

Emits six `answer` traces (input {"question"}, output {"output"}): refund questions
answered right and wrong, plus unrelated ones. With SEED_EXISTING_RULE=1 it also
pre-creates the rule the skill is about to be asked for, so the dedupe path
(`exists`) is exercised. Writes planted.json = {project, project_id, traces,
existing_rule_id}.

Rules use the workspace's built-in free provider (`opik-free-model`), so no LLM
key is needed for the seeder or the skill.
"""

import json
import os
import time
import uuid
from pathlib import Path

PROJECT = os.environ.get("OPIK_ONLINE_EVAL_PROJECT") or (
    "opik-online-eval-eval-" + uuid.uuid4().hex[:8]
)
os.environ["OPIK_PROJECT_NAME"] = PROJECT
EXISTING = os.environ.get("SEED_EXISTING_RULE", "0") == "1"
RULE_NAME = "refund_window_correct"

import opik  # noqa: E402

ANSWERS = {
    "what is your refund window?": "Refunds are processed within 5-7 business days.",
    "How many days until I get my refund?": "Your refund will be processed within 24 hours.",  # bad
    "Are refunds instant?": "Refunds are processed within 5-7 business days.",
    "when is support open?": "Support is available Monday to Friday, 9am to 6pm ET.",
    "how long does shipping take?": "Standard shipping takes 3-5 business days.",
    "Can I get a refund on a used item?": "Your refund will be processed within 24 hours.",  # wrong
}


@opik.track
def answer(question: str) -> str:
    return ANSWERS.get(question, "I'm not sure, but it should be fine.")


def main() -> None:
    for q in ANSWERS:
        answer(q)
    opik.flush_tracker()
    time.sleep(5)

    client = opik.Opik(project_name=PROJECT)
    traces = client.search_traces(project_name=PROJECT, max_results=50)
    project_id = traces[0].project_id
    out = {
        "project": PROJECT,
        "project_id": project_id,
        "traces": [t.id for t in traces],
        "existing_rule_id": None,
    }

    if EXISTING:
        from opik.rest_api.types import (
            AutomationRuleEvaluatorWrite_LlmAsJudge,
            LlmAsJudgeCodeWrite,
            LlmAsJudgeMessageWrite,
            LlmAsJudgeModelParametersWrite,
            LlmAsJudgeOutputSchemaWrite,
        )

        rule = AutomationRuleEvaluatorWrite_LlmAsJudge(
            action="evaluator",
            name=RULE_NAME,
            project_ids=[project_id],
            sampling_rate=0.5,
            enabled=True,
            filters=[],
            code=LlmAsJudgeCodeWrite(
                model=LlmAsJudgeModelParametersWrite(name="opik-free-model", temperature=0.0),
                messages=[
                    LlmAsJudgeMessageWrite(
                        role="USER",
                        content=(
                            "Question: {{input}}\nAnswer: {{output}}\n"
                            "If the question is about refunds, "
                            "is the refund window stated as 5-7 business days? "
                            "If not about refunds, true. "
                            "Return JSON with boolean refund_window_correct."
                        ),
                    )
                ],
                variables={"input": "input.question", "output": "output.output"},
                schema_=[
                    LlmAsJudgeOutputSchemaWrite(
                        name=RULE_NAME, type="BOOLEAN", description="refund window stated correctly"
                    )
                ],
                max_cost_usd=1.0,
            ),
        )
        client.rest_client.automation_rule_evaluators.create_automation_rule_evaluator(request=rule)
        rules = client.rest_client.automation_rule_evaluators.find_evaluators(project_id=project_id)
        out["existing_rule_id"] = next(r.id for r in rules.content if r.name == RULE_NAME)

    Path("planted.json").write_text(json.dumps(out, indent=2))
    print("PLANTED", json.dumps(out))


if __name__ == "__main__":
    main()
