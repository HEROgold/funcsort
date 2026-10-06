"""Custom confkit data types for funcsort.

These subclass confkit's public :class:`~confkit.BaseDataType` so a config value is read
straight into the domain type the engine consumes (``list[Group]``, ``list[MethodKind]``)
instead of a raw TOML structure that needs a second, hand-written resolution step.

confkit's TOML parser stores a value natively only when ``str(native) == str(value)``
and always hands ``convert`` the stringified form on read. TOML arrays and tables
stringify as Python literals, so every type here parses its input with
:func:`ast.literal_eval` and serialises its default back to such a literal.

A malformed value raises :class:`ValueError` from ``convert``; the loader reports it and
falls back to the option's default, so a broken config never silently drops members.

This module depends only on confkit's public API and never modifies the confkit package.
"""

from __future__ import annotations

import ast
import re
from abc import abstractmethod
from collections.abc import Iterable
from enum import StrEnum
from typing import Any, cast, override

from confkit import BaseDataType

from funcsort.groups import (
    Group,
    MemberKind,
    MethodKind,
    Scope,
    compile_matcher,
    default_groups,
)

DEFAULT_METHOD_TYPE_ORDER = (MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC)


class _LiteralValue[T](BaseDataType[T]):
    """A confkit value stored as a native TOML structure and read back from its literal."""

    @override
    def __str__(self) -> str:
        """Return a Python literal so confkit stores the value as a native TOML structure."""
        return repr(self._literal())

    @override
    def convert(self, value: Any) -> T:
        """Parse a stored value (native, or its stringified literal) into the domain type."""
        if not isinstance(value, str):
            return self._parse(value)
        try:
            return self._parse(ast.literal_eval(value))
        except (ValueError, SyntaxError):
            return self._parse(value)

    def _literal(self) -> object:
        """Return the TOML-representable form of the current value."""
        return self.value

    @abstractmethod
    def _parse(self, raw: object) -> T:
        """Build the domain value from a parsed literal (or an unparseable string)."""


class StringTuple(_LiteralValue[tuple[str, ...]]):
    """A TOML array of strings, read as a tuple (a bare comma-separated string also works)."""

    def __init__(self, default: tuple[str, ...] = ()) -> None:
        """Initialise with a default tuple (empty when omitted)."""
        super().__init__(default)

    @override
    def _parse(self, raw: object) -> tuple[str, ...]:
        """Normalise a list, a single scalar or a comma-separated string into a tuple."""
        if isinstance(raw, str):
            return tuple(item.strip() for item in raw.split(",") if item.strip())
        if isinstance(raw, Iterable):
            return tuple(str(item) for item in cast("Iterable[object]", raw))
        return (str(raw),)


class MethodTypeOrder(_LiteralValue[list[MethodKind]]):
    """The secondary ordering of method types, e.g. ``["instance", "class", "static"]``."""

    def __init__(self, default: Iterable[MethodKind] = DEFAULT_METHOD_TYPE_ORDER) -> None:
        """Initialise with a default order (instance -> class -> static when omitted)."""
        super().__init__(list(default))

    @override
    def _literal(self) -> object:
        return [str(kind) for kind in self.value]

    @override
    def _parse(self, raw: object) -> list[MethodKind]:
        """Parse every entry as a :class:`MethodKind`; an unknown entry raises ValueError."""
        return [MethodKind(value) for value in _as_tokens(raw)]


class GroupList(_LiteralValue[list[Group]]):
    """The ``[[tool.funcsort.groups]]`` array of tables, read as compiled :class:`Group` objects.

    An empty array means "no custom groups", which resolves to :func:`default_groups`.
    """

    def __init__(self) -> None:
        """Initialise with no custom groups; only this empty default is ever serialised."""
        super().__init__([])

    @override
    def _parse(self, raw: object) -> list[Group]:
        """Build each group table; any malformed entry raises ValueError."""
        if not isinstance(raw, list):
            msg = "groups must be an array of tables"
            raise ValueError(msg)  # noqa: TRY004 - confkit only reports ValueError as invalid input
        if not raw:
            return default_groups()
        try:
            return [_build_group(entry) for entry in cast("list[dict[str, Any]]", raw)]
        except (KeyError, TypeError, re.error) as exc:
            raise ValueError(str(exc)) from exc


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


def _parse_enum_set[E: StrEnum](value: Any, enum: type[E], default: frozenset[E] | None) -> frozenset[E] | None:  # noqa: ANN401
    """Parse a string/list config value into a frozenset of enum members.

    ``None`` or an ``"any"`` token resolves to ``default`` (typically "no filter").
    """
    if value is None:
        return default
    members = {enum(item) for item in _as_tokens(value) if item != "any"}  # zuban:ignore[misc]
    return frozenset(members) if members else default
