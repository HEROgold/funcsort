"""Custom herogold.argparse argument types for funcsort.

``Argument`` rejects ``default=None``, and ``Actions.STORE_BOOL`` never hands the declared
``default`` to argparse, so an option cannot express "not given". :class:`OptionalArgument`
and :class:`BoolArgument` close both gaps so the declarative CLI block stays the single
source of truth for every default. Both set ``self.default`` after ``Argument.__init__`` runs,
and ``_setup_parser_argument`` pins that value with ``parser.set_defaults``. ``BoolArgument``
cannot use a ``default_factory`` because herogold 6.1 requires a ``STORE_BOOL`` factory to
return a bool.

It never modifies the herogold package, but :class:`OptionalArgument` overrides herogold's
private ``Argument._setup_parser_argument(owner, name)`` (herogold 5), so a herogold release
that changes it can break funcsort. The ``fresh-install`` CI job catches that.
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
    def _setup_parser_argument(self, owner: type, name: str) -> None:
        """Register the option, then pin the declared default for its destination."""
        super()._setup_parser_argument(owner, name)
        parser.set_defaults(**{name: self.default})


class BoolArgument(OptionalArgument[bool]):
    """A ``--flag``/``--no-flag`` pair with a tri-state default.

    ``True``/``False`` pin the value when neither flag is passed; ``None`` leaves it unset.
    """

    def __init__(self, *names: str, default: bool | None, help: str = "") -> None:  # noqa: A002
        """Initialise a STORE_BOOL argument whose ``default`` may be ``None`` (unset)."""
        # herogold 6.1 rejects a ``None`` default and a factory that returns a non-bool for STORE_BOOL.
        # Skip ``OptionalArgument.__init__`` (it always passes a factory), hand ``Argument`` a valid
        # bool, then store the declared tri-state value for ``_setup_parser_argument`` to pin.
        Argument.__init__(self, *names, action=Actions.STORE_BOOL, default=False, help=help)
        self.default = default
