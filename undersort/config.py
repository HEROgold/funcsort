"""Configuration loading for undersort.

Configuration is read with confkit from a dedicated ``undersort.toml`` (preferred) or,
as a fallback, the ``[tool.undersort]`` table of ``pyproject.toml``. Both files use the
same ``[tool.undersort]`` section and keys. The raw values are resolved into a single
immutable :class:`Settings` value object that drives the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from confkit import Config

from undersort import logger
from undersort.config_types import GroupTable, TomlList
from undersort.groups import (
    DEFAULT_CREATIONAL_DUNDERS,
    Group,
    MemberKind,
    MethodKind,
    Scope,
    compile_matcher,
    default_groups,
    groups_for_order,
)

_CONFIG_FILENAMES = ("undersort.toml", "pyproject.toml")
_DEFAULT_ORDER = ("creational", "dunder", "public", "protected", "private")
_DEFAULT_METHOD_TYPE_ORDER = (MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC)


@dataclass(frozen=True)
class Settings:
    """Resolved undersort configuration that drives the sorter.

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


@dataclass(frozen=True)
class _RawConfig:
    """The verbatim ``[tool.undersort]`` values as read from the config file."""

    order: list[str]
    method_type_order: list[str]
    creational_dunders: list[str]
    exclude: list[str]
    sort_module: bool
    groups: list[dict[str, Any]]


def load_settings() -> Settings:
    """Load and resolve configuration into a :class:`Settings`.

    Falls back to built-in defaults when no config file or section is present, or when
    the configured groups are invalid.
    """
    config_path = _find_config_file()
    if config_path is None:
        return Settings(groups=default_groups())

    raw = _read_raw(config_path)
    return _resolve(raw, config_path)


def _find_config_file() -> Path | None:
    """Find ``undersort.toml`` (preferred) or ``pyproject.toml`` in cwd or a parent."""
    current_dir = Path.cwd()
    for directory in [current_dir, *current_dir.parents]:
        for filename in _CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.exists():
                return candidate
    return None


def _read_raw(path: Path) -> _RawConfig:
    """Read the ``[tool.undersort]`` values from ``path`` via confkit (never writes)."""

    class _Cfg(Config[Any]): ...

    _Cfg.write_on_edit = False
    _Cfg.set_file(path)

    # confkit derives the section name from the class qualname, so the nested classes
    # must be named ``tool`` -> ``undersort`` to address ``[tool.undersort]``.
    class tool:  # noqa: N801 - intentional: forms the "tool.undersort" section path
        class undersort:  # noqa: N801
            order = _Cfg(TomlList(list(_DEFAULT_ORDER)))
            method_type_order = _Cfg(TomlList([str(t) for t in _DEFAULT_METHOD_TYPE_ORDER]))
            creational_dunders = _Cfg(TomlList(list(DEFAULT_CREATIONAL_DUNDERS)))
            exclude = _Cfg(TomlList([]))
            sort_module = _Cfg(True)
            groups = _Cfg(GroupTable([]))

    section = tool.undersort
    return _RawConfig(
        order=section.order,
        method_type_order=section.method_type_order,
        creational_dunders=section.creational_dunders,
        exclude=section.exclude,
        sort_module=section.sort_module,
        groups=section.groups,
    )


def _resolve(raw: _RawConfig, path: Path) -> Settings:
    """Resolve raw values into a :class:`Settings`, with validation and fallbacks."""
    method_type_order = _parse_method_type_order(raw.method_type_order, path)
    exclude = tuple(raw.exclude)

    groups = _build_groups(raw.groups, path) if raw.groups else _legacy_groups(raw.order, raw.creational_dunders, path)

    return Settings(
        groups=groups,
        method_type_order=method_type_order,
        exclude=exclude,
        sort_module=raw.sort_module,
    )


def _build_groups(raw_groups: list[dict[str, Any]], path: Path) -> list[Group]:
    """Build groups from a user-defined ``[[tool.undersort.groups]]`` block.

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
    match = entry["match"]
    tokens = [match] if isinstance(match, str) else list(match)
    if not tokens:
        msg = f"group {name!r} has an empty 'match'"
        raise ValueError(msg)
    matchers = tuple(compile_matcher(token) for token in tokens)
    default_kinds = frozenset({MemberKind.FUNCTION})
    return Group(
        name=name,
        matchers=matchers,
        kinds=_parse_enum_set(entry.get("kind"), MemberKind, default_kinds) or default_kinds,
        types=_parse_enum_set(entry.get("type"), MethodKind, None),
        scopes=_parse_enum_set(entry.get("scope"), Scope, None),
    )


def _legacy_groups(order: list[str], creational_dunders: list[str], path: Path) -> list[Group]:
    """Build default groups honouring the legacy ``order`` key.

    Reproduces the historical behaviour where omitting ``creational``/``dunder`` from
    ``order`` folds those methods into ``public`` rather than dropping them.
    """
    valid = {g.name for g in default_groups()}
    if not all(name in valid for name in order):
        logger.warning(f"Invalid order values in {path}. Using default order.")
        order = list(_DEFAULT_ORDER)

    return groups_for_order(order, tuple(creational_dunders))


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
    raw_values = [value] if isinstance(value, str) else list(value)
    members = {enum(item) for item in raw_values if item != "any"}
    return frozenset(members) if members else default
