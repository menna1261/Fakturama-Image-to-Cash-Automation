"""
Field specifications: how to ground a given control in Fakturama's UI.

See utils/automation/README.md for the reasoning behind the three strategies
and why auto_id is deliberately excluded as a lookup key.
"""

from dataclasses import dataclass
from enum import Enum, auto


class Strategy(Enum):
    # Control exposes a stable, unique title we can query directly.
    BY_TITLE = auto()
    # Control has no usable title of its own, but sits in the same parent
    # Pane as a labeled Static text sibling — locate the label, then the
    # control within its parent.
    SIBLING_OF_LABEL = auto()
    # Control has no accessible name at all (confirmed via tree
    # inspection) — needs a vision-LLM grounding call. Not implemented
    # yet; this strategy exists as the extension point for it.
    VISION_FALLBACK = auto()


@dataclass(frozen=True)
class FieldSpec:
    name: str  # human-readable identifier, used in logging/errors
    strategy: Strategy
    title: str | None = None  # BY_TITLE
    control_type: str | None = None  # BY_TITLE, SIBLING_OF_LABEL
    label_text: str | None = None  # SIBLING_OF_LABEL
    vision_description: str | None = None  # VISION_FALLBACK
    # ComboBoxes need item selection (.select("Net")), not typed text
    # (.set_edit_text(...)) — set True for any field resolved to a
    # ComboBox control.
    is_combo: bool = False
