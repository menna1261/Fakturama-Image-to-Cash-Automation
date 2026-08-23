"""
The one vocabulary every step uses to report what happened.

Replaces the free-form status strings the flow used to pass around
("selected" / "cancelled" / "manual_review" / "found_existing" /
"creating"), which were compared with == at each call site — and which
let an ambiguous-match result fall through unchecked, so a run needing
human review still exited 0. The ambiguous case is now an exception
(ManualReviewRequired in context.py), so it cannot be ignored by
omission; these values only distinguish the ways a step can *succeed*.
"""

from enum import Enum, auto


class Outcome(Enum):
    # The record already existed and was selected/reused as-is.
    FOUND = auto()
    # The record did not exist, so we created, filled and saved it.
    CREATED = auto()
