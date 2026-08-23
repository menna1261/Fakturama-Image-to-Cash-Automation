"""
Tee everything a run prints to a file as well as the terminal.

Every diagnostic this codebase produces — the `--dump-ui` screen dumps,
the resolver's self-diagnosing lookup failures, the clipboard walk's
row-by-row output, the field-fill summaries — goes to stdout, which means
it lives in terminal scrollback and nowhere else. That's fine while
watching a run, and useless afterwards: working out why one FieldSpec
failed means either re-running the whole flow or hand-copying output out
of the console.

Writing UTF-8 explicitly is deliberate. Redirecting this program's output
with PowerShell's `>` produces UTF-16 with a BOM, which every ordinary
text tool then reads as byte soup (the repo's original run_log.txt is
exactly that, and had to be decoded before it could be searched).
"""

import logging
import sys
from contextlib import contextmanager
from pathlib import Path


class _Tee:
    """Writes to two streams at once. Only the methods `print` needs."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            stream.flush()

    def isatty(self) -> bool:
        # Report the terminal's answer, not the file's, so anything
        # deciding whether to colourise still sees a real console.
        return self._streams[0].isatty()


@contextmanager
def tee_stdout(path: str | Path | None):
    """
    Duplicate stdout and stderr into `path` for the duration of the block.

    A falsy `path` makes this a no-op, so callers can wrap a run
    unconditionally and let the flag decide.

    stderr is captured too — an unhandled traceback is the single most
    useful thing a log can contain, and it would otherwise be the one
    thing missing from it.
    """
    if not path:
        yield None
        return

    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("w", encoding="utf-8") as handle:
        original_stdout, original_stderr = sys.stdout, sys.stderr
        tee_out = _Tee(original_stdout, handle)
        tee_err = _Tee(original_stderr, handle)
        sys.stdout, sys.stderr = tee_out, tee_err

        # Swapping sys.stderr isn't enough on its own. A logging
        # StreamHandler grabs the stream object when it's constructed, and
        # resolver.py calls basicConfig() at import time — long before this
        # runs — so its handler still holds the real stderr and its output
        # would be the one thing missing from the log. That output is
        # precisely the interesting part: find_field() reports which
        # strategy failed and what it saw nearby through the logger, not
        # through print().
        rebound = _rebind_stream_handlers({original_stdout: tee_out, original_stderr: tee_err})
        try:
            yield log_path
        finally:
            for handler, stream in rebound:
                handler.setStream(stream)
            sys.stdout, sys.stderr = original_stdout, original_stderr


def _rebind_stream_handlers(replacements: dict) -> list[tuple]:
    """
    Point every already-attached StreamHandler at its tee'd stand-in.

    Returns (handler, original_stream) pairs so the caller can put them
    back — leaving a handler bound to a closed file would break every log
    call made after the block.
    """
    rebound = []
    loggers = [logging.getLogger()] + [
        logging.getLogger(name) for name in list(logging.root.manager.loggerDict)
    ]
    for logger in loggers:
        for handler in list(getattr(logger, "handlers", [])):
            stream = getattr(handler, "stream", None)
            if stream in replacements:
                rebound.append((handler, stream))
                handler.setStream(replacements[stream])
    return rebound
