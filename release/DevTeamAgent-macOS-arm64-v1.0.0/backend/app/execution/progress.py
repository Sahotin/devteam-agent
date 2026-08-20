from __future__ import annotations

from collections.abc import Callable


ProgressReporter = Callable[[int, str, str], None]


def report_progress(
    reporter: ProgressReporter | None,
    percent: int,
    step: str,
    detail: str,
) -> None:
    if reporter is not None:
        reporter(max(0, min(100, percent)), step, detail)
