"""
One module per numbered step of the image-to-cash flow.

Every module here exposes a single entry function taking the shared
Context and returning an Outcome (or None), handling its own internal
branching so that entry_point.py's main() stays a flat sequence of calls. A step
that can't proceed safely raises ManualReviewRequired rather than
returning a status string a caller might forget to check.
"""
