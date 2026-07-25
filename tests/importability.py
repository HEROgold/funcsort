"""Prove a sorted module still resolves every name it reads at import time.

Reordering is only safe if the emitted file still imports, and the cheap way to show that
would be to run it -- which means executing generated code, and that is not something a
test suite should ever do. So the question is handed to the static toolset the project
already lints with (the ``lint-public`` CI matrix): the source is written to a file and
each checker is asked whether it sees a syntax error, an unresolved name, or a name used
before it is bound. Everything else a checker reports is ignored; this is a name-resolution
oracle, not a type check. The generated source is never imported, compiled or executed.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_MODULE_NAME = "sorted_module.py"


@dataclass(frozen=True)
class Checker:
    """One external static checker, and how to recognise its diagnostics in its output."""

    command: str
    """Console-script name, resolved next to the interpreter running the tests."""

    args: tuple[str, ...]

    markers: tuple[str, ...]
    """Substrings that identify a syntax or name-resolution diagnostic. A line matching
    none of them is noise from a different rule and is discarded."""

    order_aware: bool
    """Whether the checker flags a *module-level* name used above its own definition.
    Checkers that only catch never-defined names still add value, but on their own they
    cannot witness the property these tests exist for."""

    @property
    def executable(self) -> Path | None:
        """Return the installed console script, or None when the tool is absent."""
        scripts = Path(sys.executable).parent
        for candidate in (scripts / self.command, scripts / f"{self.command}.exe"):
            if candidate.exists():
                return candidate
        return None

    def check(self, path: Path) -> Report | None:
        """Run the checker over ``path``, or return None when it is not installed.

        The working directory is the file's own directory, which is a fresh temporary one,
        so no repository-level tool configuration can leak into the result.
        """
        executable = self.executable
        if executable is None:
            return None
        # Fixed argv: the executable is resolved from the venv and the only variable part
        # is a filename pytest handed us. Nothing from the source under test reaches here.
        completed = subprocess.run(
            [str(executable), *self.args, path.name],
            capture_output=True,
            text=True,
            cwd=path.parent,
            check=False,
        )
        output = f"{completed.stdout}\n{completed.stderr}"
        return Report(
            self,
            tuple(line.strip() for line in output.splitlines() if any(marker in line for marker in self.markers)),
        )


@dataclass(frozen=True)
class Report:
    """What one checker had to say about one file."""

    checker: Checker
    diagnostics: tuple[str, ...]

    @property
    def resolves(self) -> bool:
        """Return whether the checker saw no syntax or name-resolution problem."""
        return not self.diagnostics


CHECKERS = (
    Checker(
        "ruff",
        ("check", "--isolated", "--no-cache", "--output-format", "concise"),
        ("F821", "E999", "invalid-syntax"),
        order_aware=True,
    ),
    Checker(
        "ty",
        ("check", "--output-format", "concise"),
        ("[unresolved-reference]", "[invalid-syntax]"),
        order_aware=True,
    ),
    Checker(
        "pyrefly",
        ("check",),
        ("[unknown-name]", "[unbound-name]", "[parse-error]"),
        # pyrefly resolves module-level names without regard to statement order, so it
        # stays in for syntax and never-defined names only.
        order_aware=False,
    ),
    Checker(
        "zuban",
        ("check",),
        ("[name-defined]", "[used-before-def]", "[syntax]"),
        order_aware=True,
    ),
    Checker(
        "mypy",
        ("--no-incremental", "--no-error-summary", "--show-error-codes", "--follow-imports=skip"),
        ("[name-defined]", "[used-before-def]", "[syntax]"),
        order_aware=True,
    ),
    Checker(
        "pyright",
        ("--outputjson",),
        ('"reportUndefinedVariable"', '"reportUnboundVariable"', '"reportPossiblyUnbound"'),
        order_aware=True,
    ),
)

_CACHE: dict[str, tuple[Report, ...]] = {}


def analyse(source: str, directory: Path) -> tuple[Report, ...]:
    """Run every installed checker over ``source``, writing it into ``directory`` first.

    Results are memoised on the source text, so a source asserted on twice costs one pass.
    """
    cached = _CACHE.get(source)
    if cached is not None:
        return cached

    # ``directory`` is pytest's per-test tmp_path and the file is created here, so there
    # is no pre-existing path to follow and nothing attacker-controlled to contain.
    path = directory / _MODULE_NAME
    path.write_text(source, encoding="utf-8")  # skylos: ignore[SKY-D324] fresh file in pytest tmp_path
    reports = tuple(report for checker in CHECKERS if (report := checker.check(path)) is not None)
    _CACHE[source] = reports
    return reports


def assert_resolves(source: str, directory: Path) -> None:
    """Fail unless every installed checker agrees ``source`` resolves all of its names."""
    failed = [report for report in analyse(source, directory) if not report.resolves]
    if failed:
        detail = "\n".join(f"  {report.checker.command}: {line}" for report in failed for line in report.diagnostics)
        msg = f"the sorted module does not resolve its names:\n{detail}\n\nsource:\n{source}"
        raise AssertionError(msg)
