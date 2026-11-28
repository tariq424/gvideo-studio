"""When a rung's render fails, read the log, apply one fix, and verify it.

The next rung runs only when that fix does not verify. A second failure of the
same defect is not another retry.
"""
from __future__ import annotations
from typing import Callable, Optional


def run_rung(render: Callable, log: Callable[[str], None], fixes: list, good: Callable) -> Optional[object]:
    """render(note) does the work. note collects lines. good(result) says it verified."""
    notes: list[str] = []

    def note(msg: str) -> None:
        notes.append(str(msg))
        log(msg)

    try:
        result = render(note)
    except Exception as exc:
        notes.append(f"exception: {type(exc).__name__}: {exc}")
        result = None
    if good(result):
        return result
    blob = "\n".join(notes)
    log("render failed; reading the log: " + blob[-400:])
    for fix in fixes:
        if not fix["match"](blob):
            continue
        log(f"applying fix: {fix['name']}")
        try:
            fix["apply"](blob)
        except Exception as exc:
            log(f"fix {fix['name']} could not be applied ({exc})")
            continue
        try:
            result = render(note)
        except Exception as exc:
            notes.append(f"exception: {type(exc).__name__}: {exc}")
            result = None
        if good(result):
            log(f"fix verified: {fix['name']}")
            return result
        log(f"fix not verified: {fix['name']}")
        break
    log("defect is not fixable; moving to the next rung")
    return None
