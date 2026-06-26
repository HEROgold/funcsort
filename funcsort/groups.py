"""Generic, configuration-driven sorting model for funcsort.

This module is the pure engine: it knows nothing about libcst or TOML. It defines
the value objects (:class:`Group`, :class:`Member`, :class:`Classification`) and the
:func:`classify` placement rule that drive the sorter, plus the built-in
:func:`default_groups` used when no configuration is supplied.

A :class:`Group` is matched against a member's *name* by regular expression
(first-match-wins down an ordered list), optionally narrowed by member kind, method
type, scope and decorators. Users replace the default group list via configuration to
take full control of what is sorted and in which order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

# Curated set of object/class lifecycle dunders treated as the "creational" group by
# default. Every other magic method (``__x__``) falls into the generic "dunder" group.
DEFAULT_CREATIONAL_DUNDERS: tuple[str, ...] = (
    "__new__",
    "__init__",
    "__init_subclass__",
    "__post_init__",
    "__set_name__",
)


class Scope(StrEnum):
    """Where a member lives."""

    CLASS = "class"
    MODULE = "module"


class MemberKind(StrEnum):
    """What kind of statement a sortable member is."""

    FUNCTION = "function"
    ASSIGNMENT = "assignment"


class MethodKind(StrEnum):
    """Secondary classification of a function by its binding."""

    INSTANCE = "instance"
    CLASS = "class"
    STATIC = "static"


@dataclass(frozen=True)
class Member:
    """A single sortable statement within a class or module body.

    Attributes:
        index: Original position within the body (used to minimise movement).
        node: The underlying libcst node (kept opaque to this module).
        kind: Whether the member is a function or an assignment.
        name: The function name or the assignment's target name.
        method_type: Binding of a function; :attr:`MethodKind.INSTANCE` for assignments.
        scope: Whether the member lives in a class or module body.
        decorators: Normalised, dotted decorator names applied to the member.
    """

    index: int
    node: object
    kind: MemberKind
    name: str
    method_type: MethodKind
    scope: Scope
    decorators: tuple[str, ...] = ()


@dataclass(frozen=True)
class Group:
    """An ordered bucket members are placed into, matched by name regex.

    Attributes:
        name: Human-readable identifier (used in diagnostics and bucket keys).
        matchers: Pre-compiled patterns; a member matches if *any* one searches its name.
        kinds: Member kinds this group accepts (defaults to functions only).
        types: Method types this group accepts; ``None`` means any.
        scopes: Scopes this group accepts; ``None`` means any.
        decorators: If set, the member must carry a decorator whose name matches one of
            these patterns; ``None`` means no decorator requirement.
    """

    name: str
    matchers: tuple[re.Pattern[str], ...]
    kinds: frozenset[MemberKind] = field(default_factory=lambda: frozenset({MemberKind.FUNCTION}))
    types: frozenset[MethodKind] | None = None
    scopes: frozenset[Scope] | None = None
    decorators: tuple[re.Pattern[str], ...] | None = None

    def accepts(self, member: Member) -> bool:
        """Return whether ``member`` belongs to this group."""
        if member.kind not in self.kinds:
            return False
        if self.types is not None and member.method_type not in self.types:
            return False
        if self.scopes is not None and member.scope not in self.scopes:
            return False
        if self.decorators is not None and not self._matches_decorator(member):
            return False
        return any(matcher.search(member.name) for matcher in self.matchers)

    def targets_assignments(self) -> bool:
        """Return whether this group can ever match an assignment member."""
        return MemberKind.ASSIGNMENT in self.kinds

    def _matches_decorator(self, member: Member) -> bool:
        """Return whether any of the member's decorators match this group's filter."""
        assert self.decorators is not None  # noqa: S101 - guarded by caller
        return any(pattern.search(name) for name in member.decorators for pattern in self.decorators)


@dataclass(frozen=True)
class Classification:
    """The result of placing a member into a group.

    Using a value object instead of an ``Optional[str]`` keeps call sites explicit
    and lets the placement result grow fields without churn.
    """

    member: Member
    group: Group | None

    @property
    def is_matched(self) -> bool:
        """Return whether the member was matched by any group."""
        return self.group is not None


def exact(name: str) -> re.Pattern[str]:
    """Compile a matcher that matches ``name`` exactly (whole string)."""
    return re.compile(r"\A" + re.escape(name) + r"\Z")


def compile_matcher(token: str) -> re.Pattern[str]:
    """Compile a configured ``match``/``decorator`` token.

    A bare Python identifier is treated as an exact-name match; anything else is a regex.
    """
    if token.isidentifier():
        return exact(token)
    return re.compile(token)


def _dunder_matcher(creational_dunders: tuple[str, ...] | list[str]) -> re.Pattern[str]:
    """Match any magic method (``__x__``) that is not one of the creational names."""
    if not creational_dunders:
        return re.compile(r"^__.+__$")
    alternation = "|".join(re.escape(name) for name in creational_dunders)
    return re.compile(rf"^(?!(?:{alternation})$)__.+__$")


def default_groups(creational_dunders: tuple[str, ...] | list[str] = DEFAULT_CREATIONAL_DUNDERS) -> list[Group]:
    """Return the built-in groups, in output order.

    The matchers are deliberately *mutually exclusive* so membership is independent of
    the groups' order (only the output order changes when a user reorders them):
    creational (exact names) -> dunder (other ``__x__``) -> public (no leading
    underscore) -> protected (single leading underscore) -> private (leading ``__``,
    non-magic).
    """
    return [
        Group("creational", tuple(exact(name) for name in creational_dunders)),
        Group("dunder", (_dunder_matcher(creational_dunders),)),
        Group("public", (re.compile(r"^[^_]"),)),
        Group("protected", (re.compile(r"^_([^_]|$)"),)),
        # Leading ``__`` that is not a magic method. Excludes exactly the dunder pattern
        # so creational/dunder/private together cover every ``__``-prefixed name.
        Group("private", (re.compile(r"^(?!__.+__$)__"),)),
    ]


def classify(member: Member, groups: list[Group]) -> Classification:
    """Place ``member`` into the first group that accepts it (first-match-wins)."""
    for group in groups:
        if group.accepts(member):
            return Classification(member, group)
    return Classification(member, None)
