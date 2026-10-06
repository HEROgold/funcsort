"""Custom herogold.argparse argument types for funcsort.

``Argument`` rejects ``default=None``, and ``Actions.STORE_BOOL`` never hands the declared
``default`` to argparse, so an option cannot express "not given". :class:`OptionalArgument`
and :class:`BoolArgument` close both gaps so the declarative CLI block stays the single
source of truth for every default.

This module depends only on herogold's public API and never modifies the herogold package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from herogold.argparse import Actions, Argument, parser
from herogold.sentinel import MISSING

if TYPE_CHECKING:
    from herogold.argparse.argument import ArgumentType


class OptionalArgument[T](Argument[T | None]):
    """An argument whose default may be ``None``, meaning "unset".

    An unset option lets a later source (e.g. the config file) decide its value.
    """

    def __init__(
        self,
        *names: str,
        type_: ArgumentType[T] = MISSING,
        action: Actions = Actions.STORE,
        default: T | None,
        help: str = "",  # noqa: A002
    ) -> None:
        """Initialise an argument whose ``default`` may be ``None`` (unset)."""
        # ``default_factory`` is the only route through ``Argument.resolve_default`` that accepts None.
        super().__init__(*names, type_=type_, action=action, default_factory=lambda: default, help=help)

    @override
    def _setup_parser_argument(self, name: str) -> None:
        """Register the option, then pin the declared default for its destination."""
        super()._setup_parser_argument(name)
        parser.set_defaults(**{name: self.default})


class BoolArgument(OptionalArgument[bool]):
    """A ``--flag``/``--no-flag`` pair with a tri-state default.

    ``True``/``False`` pin the value when neither flag is passed; ``None`` leaves it unset.
    """

    def __init__(self, *names: str, default: bool | None, help: str = "") -> None:  # noqa: A002
        """Initialise a STORE_BOOL argument whose ``default`` may be ``None`` (unset)."""
        super().__init__(*names, action=Actions.STORE_BOOL, default=default, help=help)
