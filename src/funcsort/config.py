"""Configuration loading for funcsort.

Configuration is read with confkit from a dedicated ``funcsort.toml`` (preferred) or,
as a fallback, the ``[tool.funcsort]`` table of ``pyproject.toml``. Both files use the
same ``[tool.funcsort]`` section and keys. The values are resolved into a single
immutable :class:`Settings` value object that drives the engine.

The confkit container class ``tool.funcsort`` is the single source of truth for the
config shape; values are read directly off it (no parallel schema definition).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from .config_types import GroupTable, TomlList
from confkit import Config

from funcsort.groups import (
    Group,
    MemberKind,
    MethodKind,
    Scope,
    compile_matcher,
    default_groups,
)
from . import logger

_CONFIG_FILENAMES = ("funcsort.toml", "pyproject.toml")
_DEFAULT_METHOD_TYPE_ORDER = (MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC)


@dataclass(frozen=True)
class Settings:
    """Resolved funcsort configuration that drives the sorter.

    Attributes:
        groups: Ordered groups; output order and membership rules for members.
        method_type_order: Secondary ordering of method types within each group.
        exclude: Glob patterns of files/directories to skip.
        sort_module: Whether module-level functions are sorted.
    """

    groups: list[Group]
    method_type_order: list[MethodKind] = field(
        default_factory=lambda: list(_DEFAULT_METHOD_TYPE_ORDER),
    )
    exclude: tuple[str, ...] = ()
    sort_module: bool = True


def load_settings() -> Settings:
    """Load and resolve configuration into a :class:`Settings`.

    Falls back to built-in defaults when no config file or section is present, or when
    the configured groups are invalid.
    """
    path = find_config_file()
    if path is None:
        return Settings(groups=default_groups())

    class _Cfg(Config[Any]): ...

    _Cfg.write_on_edit = False
    _Cfg.set_file(path)

    # confkit derives the section name from the class qualname, so the nested classes
    # must be named ``tool`` -> ``funcsort`` to address ``[tool.funcsort]``. This class
    # is the single definition of the config shape.
    class tool:  # noqa: N801 - intentional: forms the "tool.funcsort" section path
        class funcsort:  # noqa: N801
            method_type_order = _Cfg(TomlList([str(t) for t in _DEFAULT_METHOD_TYPE_ORDER]))
            exclude = _Cfg(TomlList([]))
            sort_module = _Cfg(True)
            groups = _Cfg(GroupTable([]))

    cfg = tool.funcsort
    return Settings(
        groups=_build_groups(cfg.groups, path) if cfg.groups else default_groups(),
        method_type_order=_parse_method_type_order(cfg.method_type_order, path),
        exclude=tuple(cfg.exclude),
        sort_module=cfg.sort_module,
    )


def find_config_file() -> Path | None:
    """Find ``funcsort.toml`` (preferred) or ``pyproject.toml`` in cwd or a parent."""
    current_dir = Path.cwd()
    for directory in [current_dir, *current_dir.parents]:
        for filename in _CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.exists():
                return candidate
    return None


def _build_groups(raw_groups: list[dict[str, Any]], path: Path) -> list[Group]:
    """Build groups from a user-defined ``[[tool.funcsort.groups]]`` block.

    On any malformed entry, warn and fall back to the built-in default groups so a
    broken config never silently drops members.
    """
    try:
        return [_build_group(entry) for entry in raw_groups]
    except (KeyError, ValueError, TypeError, re.error) as exc:
        logger.warning(f"Invalid groups in {path}: {exc}. Using default groups.")
        return default_groups()


def _build_group(entry: dict[str, Any]) -> Group:
    """Build a single :class:`Group` from a raw config table."""
    name = entry["name"]
    tokens = _as_tokens(entry["match"])
    if not tokens:
        msg = f"group {name!r} has an empty 'match'"
        raise ValueError(msg)
    decorator_tokens = _as_tokens(entry.get("decorator"))
    decorators = tuple(compile_matcher(token) for token in decorator_tokens) if decorator_tokens else None
    default_kinds = frozenset({MemberKind.FUNCTION})
    return Group(
        name=name,
        matchers=tuple(compile_matcher(token) for token in tokens),
        kinds=_parse_enum_set(entry.get("kind"), MemberKind, default_kinds) or default_kinds,
        types=_parse_enum_set(entry.get("type"), MethodKind, None),
        scopes=_parse_enum_set(entry.get("scope"), Scope, None),
        decorators=decorators,
    )


def _as_tokens(value: Any) -> list[str]:  # noqa: ANN401 - TOML scalar or list
    """Normalise a string-or-list config value into a list of strings."""
    if value is None:
        return []
    return [value] if isinstance(value, str) else list(value)


def _parse_method_type_order(raw: list[str], path: Path) -> list[MethodKind]:
    """Parse and validate the method type order, falling back to the default."""
    try:
        return [MethodKind(value) for value in raw]
    except ValueError:
        logger.warning(f"Invalid method_type_order values in {path}. Using default.")
        return list(_DEFAULT_METHOD_TYPE_ORDER)


def _parse_enum_set[E: StrEnum](value: Any, enum: type[E], default: frozenset[E] | None) -> frozenset[E] | None:  # noqa: ANN401
    """Parse a string/list config value into a frozenset of enum members.

    ``None`` or an ``"any"`` token resolves to ``default`` (typically "no filter").
    """
    if value is None:
        return default
    members = {enum(item) for item in _as_tokens(value) if item != "any"}
    return frozenset(members) if members else default
