---
kind: context
flow: compare-experiments
symptom: "list('experiment', fields=['prompt_version.commit']) is refused"
decided: 2026-11-23, found by /dogfood, OPIK-8948
recheck_when: src/opik_mcp/read_list/entities/experiment.py
---
prompt_version, dataset_version and assertion_runs are labels the server derives for the list, so their schema sub-fields are refused by design; read('experiment') has the object.
