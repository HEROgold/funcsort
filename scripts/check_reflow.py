"""Reject commits containing reflow-only hunks.

A *reflow-only* hunk changes lines without changing a single word: re-wrapped prose,
re-indented code, or whitespace churn. Such hunks bloat diffs and hide the real change,
so this pre-commit hook fails when any staged hunk consists solely of them.

Commit with ``--no-verify`` when a reflow is intentional (e.g. a deliberate re-wrap).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from enum import IntEnum

_FILE_HEADER = re.compile(r"^\+\+\+ b/(?P<path>.+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")


class ExitCode(IntEnum):
    """Process exit status understood by pre-commit."""

    OK = 0
    REFLOW_FOUND = 1


@dataclass(frozen=True)
class Hunk:
    """A single zero-context hunk of a unified diff.

    Attributes:
        path: File the hunk belongs to, relative to the repository root.
        line: First line of the hunk in the new version of the file.
        removed: Lines the hunk deletes, without the leading ``-``.
        added: Lines the hunk inserts, without the leading ``+``.

    """

    path: str
    line: int
    removed: tuple[str, ...]
    added: tuple[str, ...]

    @property
    def is_reflow(self) -> bool:
        """Whether the hunk rearranges whitespace while keeping every word identical."""
        if not self.removed or not self.added or self.removed == self.added:
            return False
        return _words(self.removed) == _words(self.added)


@dataclass(frozen=True)
class ReflowReport:
    """Reflow-only hunks found in a diff."""

    reflows: tuple[Hunk, ...]

    @property
    def exit_code(self) -> ExitCode:
        """The status the hook should exit with."""
        return ExitCode.REFLOW_FOUND if self.reflows else ExitCode.OK


def parse_hunks(diff: str) -> tuple[Hunk, ...]:
    """Split a ``git diff -U0`` output into its hunks."""
    hunks: list[Hunk] = []
    path = ""
    line = 0
    removed: list[str] = []
    added: list[str] = []
    in_hunk = False

    def flush() -> None:
        if in_hunk:
            hunks.append(Hunk(path, line, tuple(removed), tuple(added)))
        removed.clear()
        added.clear()

    for raw in diff.splitlines():
        if file_match := _FILE_HEADER.match(raw):
            flush()
            in_hunk = False
            path = file_match["path"]
        elif hunk_match := _HUNK_HEADER.match(raw):
            flush()
            in_hunk = True
            line = int(hunk_match["start"])
        elif in_hunk and raw.startswith("-"):
            removed.append(raw[1:])
        elif in_hunk and raw.startswith("+"):
            added.append(raw[1:])
        elif not raw.startswith("\\"):  # "\ No newline at end of file" belongs to the hunk
            flush()
            in_hunk = False
    flush()
    return tuple(hunks)


def find_reflows(diff: str) -> ReflowReport:
    """Collect the reflow-only hunks of a ``git diff -U0`` output."""
    return ReflowReport(tuple(hunk for hunk in parse_hunks(diff) if hunk.is_reflow))


def staged_diff() -> str:
    """Return the staged changes as a zero-context unified diff."""
    return subprocess.run(
        ["git", "diff", "--cached", "-U0", "--no-color", "--no-ext-diff"],  # noqa: S607 - git is resolved from PATH by design
        capture_output=True,
        check=True,
        text=True,
        encoding="utf-8",
    ).stdout


def main() -> ExitCode:
    """Report reflow-only hunks in the staged changes."""
    report = find_reflows(staged_diff())
    for hunk in report.reflows:
        print(f"{hunk.path}:{hunk.line}: reflow-only change (whitespace/wrapping, no words changed)")  # noqa: T201
    if report.reflows:
        print("Revert these hunks, or commit with --no-verify if the reflow is intentional.")  # noqa: T201
    return report.exit_code


def _words(lines: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(word for line in lines for word in line.split())


if __name__ == "__main__":
    sys.exit(main())
