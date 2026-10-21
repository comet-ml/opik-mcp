from __future__ import annotations

from pathlib import Path

import pytest

from tests.asserts import assert_answer_equals


def test_a_differing_answer_fails_with_one_line_naming_the_diff_file(tmp_path: Path) -> None:
    artefact = tmp_path / "answer.diff"
    with pytest.raises(pytest.fail.Exception) as failure:
        assert_answer_equals({"rows": [1, 2, 3]}, {"rows": [1, 2, 4]}, artefact=artefact)
    message = str(failure.value)
    assert str(artefact) in message, message
    assert "\n" not in message, "the message is one line; the diff belongs in the file"
    diff = artefact.read_text()
    assert "-    4" in diff, diff
    assert "+    3" in diff, diff


def test_an_equal_answer_writes_nothing(tmp_path: Path) -> None:
    artefact = tmp_path / "answer.diff"
    assert_answer_equals("same", "same", artefact=artefact)
    assert not artefact.exists()
