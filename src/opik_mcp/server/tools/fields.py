from __future__ import annotations

# OPIK-8399. Both descriptions are load-bearing and both are counted against
# the tool-surface budget, so each says the three things an agent cannot infer
# — where the names come from, that arrays are whole, and that the answer will
# say it was projected — and nothing else. Named constants rather than inline
# strings so a test can weigh them.
FIELDS_READ_DESCRIPTION = (
    "Return only these paths of the record, uncut: dotted into nested objects "
    "('trace.output'), arrays whole ('spans'). The record's id is always kept; "
    "an unknown path is refused with the valid ones. The answer says it was "
    "projected and what it omitted. Omit for the whole record."
)
FIELDS_LIST_DESCRIPTION = (
    "Return only these columns, uncut: any name from the page's 'fields:' line "
    "('data.question', 'feedback_scores.helpfulness', 'usage.total_tokens'). "
    "Each row keeps the id that opens the next level. An unknown name is "
    "refused with the valid ones. Omit for the table's own columns."
)
