"""Custom herogold.argparse argument types for funcsort.

``Actions.STORE_BOOL`` registers a ``--flag``/``--no-flag`` pair but never hands the declared
``default`` to argparse, and ``Argument`` rejects ``default=None``, so a boolean flag cannot
express "not given". :class:`BoolArgument` closes both gaps so the declarative CLI block stays
the single source of truth for every default.

This module depends only on herogold's public API and never modifies the herogold package.
"""

from __future__ import annotations

from typing import override

from herogold.argparse import Actions, Argument, parser


class BoolArgument(Argument[bool | None]):
    """A ``--flag``/``--no-flag`` pair with a tri-state default.

    ``True``/``False`` pin the value when neither flag is passed; ``None`` leaves it unset so a
    later source (e.g. the config file) decides.
    """

    def __init__(self, *names: str, default: bool | None, help: str = "") -> None:  # noqa: A002
        """Initialise a STORE_BOOL argument whose ``default`` may be ``None`` (unset)."""
        # ``default_factory`` is the only route through ``Argument.resolve_default`` that accepts None.
        super().__init__(*names, action=Actions.STORE_BOOL, default_factory=lambda: default, help=help)

    @override
    def _setup_parser_argument(self, name: str) -> None:
        """Register the flag pair, then pin the declared default for its destination."""
        super()._setup_parser_argument(name)
        parser.set_defaults(**{name: self.default})
