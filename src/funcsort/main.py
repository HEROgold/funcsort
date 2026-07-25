"""Main entry point for funcsort CLI."""

from __future__ import annotations

import fnmatch
import sys
from pathlib import Path

from herogold.argparse import Actions, Argument, parser

from funcsort.config import Settings, load_settings
from funcsort.sorter import sort_file

from . import logger

_EXCLUDE_DIRS = {"venv", "__pycache__", "node_modules"}


class _Cli:
    """Declarative CLI flags; defining them registers the options on the shared parser."""

    check = Argument("check", action=Actions.STORE_BOOL, default=False, help="Check without modifying files")
    diff = Argument("diff", action=Actions.STORE_BOOL, default=False, help="Show a diff of the changes")
    recursive = Argument("recursive", action=Actions.STORE_BOOL, default=True, help="Recurse into directories")
    sort_module = Argument("sort-module", action=Actions.STORE_BOOL, default=True, help="Sort module-level functions")
    respect_dependencies = Argument(
        "respect-dependencies",
        action=Actions.STORE_BOOL,
        default=True,
        help="Never move a definition above code that uses it at import time",
    )
    exclude = Argument[list[str]](
        "exclude",
        action=Actions.APPEND,
        default=[],
        help="Exclude files/dirs matching a glob pattern",
    )


# Defining ``_Cli`` registers the Argument descriptors on the shared parser via
# ``__set_name__``; the class itself is not needed afterwards.
del _Cli

# STORE_BOOL registers a --flag/--no-flag pair without an explicit default, so pin the
# real defaults here. sort_module and respect_dependencies default to None so the config
# value wins unless the user passes the flag explicitly.
parser.description = "Sort class methods and module-level functions into configurable groups"
parser.set_defaults(check=False, diff=False, recursive=True, sort_module=None, respect_dependencies=None)
parser.add_argument("paths", nargs="+", type=Path, help="Python files or directories to sort")


def collect_python_files(path: Path, recursive: bool = True, exclude_patterns: list[str] | None = None) -> list[Path]:
    """Collect Python files from a file or directory path.

    Args:
        path: File or directory to search.
        recursive: Whether to descend into subdirectories.
        exclude_patterns: Glob patterns to skip.

    Returns:
        Sorted list of matching ``.py`` file paths.
    """
    if path.is_file():
        return [path] if path.suffix == ".py" else []

    if not path.is_dir():
        return []

    pattern = "**/*.py" if recursive else "*.py"
    files = [
        f
        for f in path.glob(pattern)
        if not any(part in _EXCLUDE_DIRS or (part.startswith(".") and part != ".") for part in f.parts)
    ]
    if exclude_patterns:
        files = [f for f in files if not _matches_any_pattern(f, exclude_patterns)]
    return sorted(files)


def main() -> int:
    """Run the funcsort CLI."""
    args = parser.parse_args()
    settings = load_settings()

    sort_module = settings.sort_module if args.sort_module is None else args.sort_module
    respect_dependencies = settings.respect_dependencies if args.respect_dependencies is None else args.respect_dependencies
    exclude_patterns = _resolve_exclude(settings, args.exclude)

    all_files: list[Path] = []
    for path in args.paths:
        if not path.exists():
            logger.error(f"Path not found: {path}")
            continue
        all_files.extend(collect_python_files(path, args.recursive, exclude_patterns))

    if not all_files:
        logger.warning("No Python files found")
        return 0

    modified_files: list[Path] = []
    unmatched_names: set[str] = set()
    errors = False
    for file_path in all_files:
        try:
            result = sort_file(
                file_path,
                groups=settings.groups,
                method_type_order=settings.method_type_order,
                sort_module=sort_module,
                check_only=args.check,
                show_diff=args.diff,
                respect_dependencies=respect_dependencies,
            )
        except Exception as e:  # noqa: BLE001 - report and continue across files
            logger.error(f"Error processing {file_path}: {e}")
            errors = True
            continue

        unmatched_names.update(member.name for member in result.unmatched)
        if not result.modified:
            continue
        modified_files.append(file_path)
        if not args.check:
            logger.success(f"Sorted {file_path}")

    _report_unmatched(unmatched_names)
    return _report(modified_files, errors, check_only=args.check)


def _matches_any_pattern(file_path: Path, patterns: list[str]) -> bool:
    """Return whether ``file_path`` matches any of the glob ``patterns``."""
    path_str = str(file_path)
    for pattern in patterns:
        if fnmatch.fnmatch(path_str, pattern):
            return True
        if "/" not in pattern:
            if fnmatch.fnmatch(file_path.name, pattern):
                return True
            continue
        if fnmatch.fnmatch(path_str, f"*/{pattern}"):
            return True
        for part_idx in range(len(file_path.parts)):
            subpath = str(Path(*file_path.parts[part_idx:]))
            if fnmatch.fnmatch(subpath, pattern):
                return True
    return False


def _resolve_exclude(settings: Settings, cli_exclude: list[str] | None) -> list[str] | None:
    """Merge config and CLI exclusion patterns."""
    patterns = [*settings.exclude, *(cli_exclude or [])]
    return patterns or None


def _report_unmatched(unmatched_names: set[str]) -> None:
    """Warn about members that matched no group and were moved to the end."""
    if unmatched_names:
        names = ", ".join(sorted(unmatched_names))
        logger.warning(f"Members matched no group and were moved to the end: {names}. Configure a group for them.")


def _report(modified_files: list[Path], errors: bool, *, check_only: bool) -> int:
    """Emit a summary and return the process exit code."""
    if check_only and modified_files:
        logger.warning(f"Files that need sorting: {len(modified_files)}")
        for f in modified_files:
            logger.console.print(f"  - {f}")
        return 1

    if not modified_files and not errors:
        logger.info("All files are already sorted correctly")
    elif modified_files and not check_only:
        logger.success(f"Sorted {len(modified_files)} file(s) successfully")

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
