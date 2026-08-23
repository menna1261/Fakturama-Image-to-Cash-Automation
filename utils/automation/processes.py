"""
Finding and killing processes by executable name, via psutil.

Used at startup to clear away a leftover Fakturama from an earlier run
(a half-open editor, a modal dialog someone left on screen, or a crashed
instance still holding its workspace lock) before launching a fresh one.

Note this is a hard kill — `Process.kill()` is `TerminateProcess` on
Windows, so Fakturama gets no chance to run its own shutdown. Its
embedded database can therefore be left with stale lock files that the
next launch has to clean up. That's the accepted trade for a startup
cleanup that always works, including on an instance that's hung or
sitting behind a modal "save changes?" prompt (which is exactly the
state a polite close request can't get past).
"""

import logging

import psutil

logger = logging.getLogger("automation.processes")

# How long to wait for killed processes to actually disappear.
_EXIT_TIMEOUT = 10


def find_processes(image_name: str) -> list[psutil.Process]:
    """
    Every running process whose executable name matches `image_name`
    (case-insensitively).

    Returns all matches, not just the first: a leftover instance plus a
    freshly launched one is exactly the situation this module exists to
    clean up, so it has to be able to see both. Matching is on the whole
    name rather than a substring — this list feeds straight into kill(),
    and a substring would happily match a neighbouring process too.
    """
    needle = image_name.lower()
    found = []
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"]
            if name and name.lower() == needle:
                found.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            # It exited while we were iterating, or belongs to another
            # user — either way it isn't ours to worry about.
            continue
    return found


def find_pids(image_name: str) -> list[int]:
    return [proc.pid for proc in find_processes(image_name)]


def close_processes(image_name: str, *, timeout: float = _EXIT_TIMEOUT) -> list[int]:
    """
    Kill every running instance of `image_name` and return the PIDs that
    were killed (empty if none were running).

    Raises RuntimeError if anything survives — an instance we lack the
    rights to kill is not something to quietly proceed past, since the
    new instance would then be racing it for the workspace lock.
    """
    procs = find_processes(image_name)
    if not procs:
        print(f"No existing {image_name} process to close.")
        return []

    pids = [proc.pid for proc in procs]
    print(f"Killing {len(procs)} existing {image_name} process(es): {pids}")

    for proc in procs:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            # Already gone — that's the outcome we wanted anyway.
            logger.info("PID %s had already exited", proc.pid)
        except psutil.AccessDenied:
            print(f"WARNING: access denied killing {image_name} (PID {proc.pid}).")

    # Wait on every process we found, not just the ones kill() accepted:
    # an access-denied instance is still running and still has to be
    # reported below.
    _, alive = psutil.wait_procs(procs, timeout=timeout)
    if alive:
        raise RuntimeError(
            f"Could not close {image_name} (PIDs {[p.pid for p in alive]}). Close it "
            f"by hand, or run with --attach to drive the instance that's already open."
        )

    print(f"Closed {len(pids)} existing {image_name} process(es).")
    return pids
