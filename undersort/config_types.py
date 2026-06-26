"""Custom confkit data types for undersort.

These subclass confkit's public :class:`~confkit.BaseDataType` so undersort can store
*native* TOML arrays and array-of-tables (``[[tool.undersort.groups]]``) instead of
confkit's default stringified representation.

confkit's TOML parser stores a value natively only when ``str(native) == str(value)``
and always hands ``convert`` the stringified form on read. By making ``__str__`` the
Python ``repr`` of the native value, these types round-trip cleanly: confkit writes
idiomatic TOML, and ``convert`` parses the repr back with :func:`ast.literal_eval`.

This module depends only on confkit's public API and never modifies the confkit package.
"""

from __future__ import annotations

import ast
from typing import Any, cast, override

from confkit import BaseDataType


class TomlList(BaseDataType[list[Any]]):
    """A confkit value that is a native TOML array (round-trips as a Python list)."""

    def __init__(self, default: list[Any] | None = None) -> None:
        """Initialise with a default list (empty when omitted)."""
        super().__init__(default if default is not None else [])

    @override
    def __str__(self) -> str:
        """Return a repr so confkit stores the value as a native TOML array."""
        return repr(self.value)

    @override
    def convert(self, value: Any) -> list[Any]:  # noqa: ANN401 - confkit hands us str or native
        """Parse a stored value back into a list, tolerating native and legacy forms."""
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return [value]
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            # Legacy comma-separated fallback (confkit's historical List format).
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(parsed, (list, tuple)):
            return list(parsed)
        return [parsed]


class GroupTable(BaseDataType[list[dict[str, Any]]]):
    """A confkit value holding a list of group tables (``[[tool.undersort.groups]]``)."""

    def __init__(self, default: list[dict[str, Any]] | None = None) -> None:
        """Initialise with a default list of group dicts (empty when omitted)."""
        super().__init__(default if default is not None else [])

    @override
    def __str__(self) -> str:
        """Return a repr so confkit stores the value as native TOML tables."""
        return repr(self.value)

    @override
    def convert(self, value: Any) -> list[dict[str, Any]]:  # noqa: ANN401 - str or native
        """Parse a stored value back into a list of group dicts."""
        if isinstance(value, list):
            return value
        if not isinstance(value, str):
            return []
        try:
            parsed = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return []
        if not isinstance(parsed, list):
            return []
        return cast("list[dict[str, Any]]", parsed)
