# Field grounding strategy

Fakturama's UI controls don't all expose the same identifying
information via UIA, so `find_field()` (in `resolver.py`) dispatches on
a per-field `Strategy` rather than assuming one lookup method works
everywhere.

## Why `auto_id` is never used

Fakturama assigns `automation_id` values that are **not stable across
app restarts** — the same field ("Cust.Ref.") was confirmed empirically
to expose a different `auto_id` on two separate launches of the app.
Since our automation restarts/reattaches to Fakturama across runs, any
lookup keyed on `auto_id` would work once and then silently break on
the next run. Every strategy below is built only from properties that
are stable across restarts: `title`, `control_type`, and structural
position (parent/sibling relationships).

## The three strategies

### 1. `BY_TITLE`

For controls that expose a real, unique `title` — e.g. the Cust.Ref.
field reports `title="Cust.Ref."`, `control_type="Edit"`. This is the
simplest and most direct case: `scope.child_window(title=..., control_type=...)`.

Prefer this whenever tree inspection shows a non-empty, distinguishing
title.

### 2. `SIBLING_OF_LABEL`

Some controls report `title=""` — no usable name of their own — but sit
in the same parent `Pane` as a labeled `Static` text element (e.g. the
Date field: an unlabeled `Edit` next to a `Text` control whose title is
`"Date"`). For these, we locate the label first, walk up to its
`.parent()`, and search for the target control type within that same
parent. This only works because the label and its field are structural
siblings in Fakturama's layout — confirmed via tree inspection, not
assumed.

### 3. `VISION_FALLBACK`

Icon-only toolbar buttons with no accessible name at all (confirmed via
`print_control_identifiers` — genuinely blank, not just untested) can't
be grounded through UIA properties or structure. These are marked
`VISION_FALLBACK` with a `vision_description` describing what a human
(or eventually a vision-LLM call) should look for — e.g. "the upper
icon beside 'Invoice address' ... NOT the lower green plus icon".

This strategy is **not implemented yet**. `find_field()` raises
`NotImplementedError` with the field's `vision_description` when it
hits this branch, so callers get a clear, actionable message instead of
an attribute error or silent failure. The actual Gemini vision call
will be wired into `_find_via_vision_fallback()` in `resolver.py` later
— the extension point is deliberately isolated there so nothing else
needs to change when it lands.

## Adding a new field

1. Run the app, get to the target screen, dump its control tree.
2. Check the target control's `title`. If it's non-empty and unique on
   that screen, use `BY_TITLE`.
3. If `title` is empty, look for a labeled sibling in the same parent
   pane. If one exists, use `SIBLING_OF_LABEL`.
4. If neither applies (icon-only control, no name, no labeled sibling),
   use `VISION_FALLBACK` and write a precise `vision_description` —
   include disambiguating details (e.g. "NOT the green plus icon")
   since that description is the only grounding signal available for
   that field.
5. Add the entry to the relevant dict in `field_specs.py` only after
   verifying it against a real running instance — don't add speculative
   entries for screens you haven't inspected yet.
