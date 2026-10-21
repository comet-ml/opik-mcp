"""How many cases make a mean worth acting on.

Two entities ask. The experiment listing, ranked by a score, asks whether the
first two rows are far enough apart over enough cases to be an order at all;
the per-case comparison asks whether two runs covered enough cases to be
weighed against each other. Neither may import the other, so the number they
have to agree on lives here, the way a fact two entities need always does.
"""

from __future__ import annotations

from typing import Final

THIN_SAMPLE: Final = 10
"""Below this many cases a mean is a hint, and a gap between two means is not
a ranking. Not a statistical threshold; a plain one, chosen so the runs seen
live (one, two, three cases) trip it and a twenty-case evaluation does not."""


def is_thin(count: int) -> bool:
    return count < THIN_SAMPLE


__all__ = ["THIN_SAMPLE", "is_thin"]
