"""What the compared experiments have to agree on: one dataset to refuse on,
and the version, status and case count to warn on.
"""

from __future__ import annotations

from typing import Final

from opik_mcp.read_list.entities.dataset.compared_row import Experiment
from opik_mcp.read_list.errors import EntityArgValidationError
from opik_mcp.read_list.sample import is_thin

#: opik-backend's ``ExperimentStatus.RUNNING``. The record's ``status`` is not
#: a filterable field, so it has no entry in the OQL enum table; the one value
#: the comparison has to recognise is named here.
RUNNING: Final = "running"


def guards(experiments: list[Experiment]) -> list[str]:
    """What makes this comparison unsafe to read at face value, before the table.

    Each was a check the agent had to make itself with a ``list('experiment')``
    call before comparing, and skipped, because the table renders either way.
    Three facts decide whether two averages are the same kind of number: the
    dataset version each run used (same dataset, different version means
    cases were added, edited or removed, so a dash may be a case that did not
    exist yet — a warning, not the refusal a different *dataset* gets, since
    the shared cases still line up); whether a run has finished (a running
    one's averages will move); and how many cases each covered (the incident
    behind ``experiment._SPINE`` — a mean over three beside a mean over
    twenty is not a comparison of the same thing).

    Every guard stays silent when a record lacks the field it reads. Older
    experiments carry no ``dataset_version_id`` and some carry no
    ``trace_count``; a guard that fired on absence would warn about a
    difference nobody can see.
    """
    if len(experiments) < 2:
        return []
    notes: list[str] = []

    versions = {e.dataset_version_id for e in experiments if e.dataset_version_id}
    if len(versions) > 1:
        ran = ", ".join(
            f"{e.label} ran {e.dataset_version or e.dataset_version_id or 'an unknown version'}"
            for e in experiments
        )
        notes.append(
            f"These runs used different versions of the dataset ({ran}): cases may have been "
            "added, edited or removed between them, so a - can mean the case did not exist yet, "
            "and a gap on an edited case is not a regression."
        )

    running = [e.label for e in experiments if e.status == RUNNING]
    if running:
        who = " and ".join(running)
        verb = "is" if len(running) == 1 else "are"
        notes.append(
            f"{who} {verb} still running: {'its' if len(running) == 1 else 'their'} scores are "
            "over the cases finished so far and will change."
        )

    counts = [(e.label, e.trace_count) for e in experiments if e.trace_count is not None]
    if len(counts) == len(experiments) and len({n for _, n in counts}) > 1:
        each = ", ".join(f"{label} {n}" for label, n in counts)
        smallest = min(n for _, n in counts)
        thin = f" {smallest} is too few to weigh against the others." if is_thin(smallest) else ""
        notes.append(f"The runs covered different numbers of cases ({each}).{thin}")
    return notes


def dataset_of(experiments: list[Experiment], dataset_id: str | None) -> str:
    """The one dataset every compared experiment ran.

    Experiments of different datasets have no cases in common, so lining them
    up would produce a table of blanks rather than an answer. The UI refuses
    the same comparison in the same words.
    """
    dataset_ids = {experiment.dataset_id for experiment in experiments}
    if len(dataset_ids) > 1:
        ran = "; ".join(
            f"{experiment.name} ran {experiment.dataset_name} ({experiment.dataset_id})"
            for experiment in experiments
        )
        raise EntityArgValidationError(
            f"Cannot compare experiments that ran different datasets: {ran}. "
            "Compare experiments of one dataset."
        )
    ran_dataset_id = experiments[0].dataset_id
    if dataset_id and dataset_id != ran_dataset_id:
        raise EntityArgValidationError(
            f"dataset_id {dataset_id!r} is not the dataset these experiments ran "
            f"({experiments[0].dataset_name}, {ran_dataset_id}). Drop dataset_id: with "
            "experiment_ids the dataset is resolved from the experiments."
        )
    return ran_dataset_id
