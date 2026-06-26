"""Property-based tests for the sorting engine invariants."""

from __future__ import annotations

import keyword

import libcst as cst
from hypothesis import given
from hypothesis import strategies as st

from undersort.groups import (
    DEFAULT_CREATIONAL_DUNDERS,
    Member,
    MemberKind,
    MethodKind,
    Scope,
    classify,
    default_groups,
    groups_for_order,
)
from undersort.sorter import _sort_block, get_method_visibility

_PREFIXES = ["", "_", "__", "__dunder_"]
_DECORATORS = ["", "@classmethod\n", "@staticmethod\n"]
_DEFAULT_MTO = [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]


def _legacy_visibility(name: str) -> str:
    """Independent reference implementation of the historical visibility cascade."""
    if name.startswith("__") and name.endswith("__"):
        return "creational" if name in DEFAULT_CREATIONAL_DUNDERS else "dunder"
    if name.startswith("__"):
        return "private"
    if name.startswith("_"):
        return "protected"
    return "public"


@st.composite
def _identifiers(draw: st.DrawFn) -> str:
    """Generate valid identifiers with a letter-bearing body (no degenerate all-underscore)."""
    prefix = draw(st.sampled_from(_PREFIXES))
    body = draw(st.text(alphabet="abcdefghijklmnop", min_size=1, max_size=6))
    suffix = draw(st.sampled_from(["", "__"]))
    name = f"{prefix}{body}{suffix}"
    if not name.isidentifier() or keyword.iskeyword(name):
        name = f"m_{body}"
    return name


@st.composite
def _class_source(draw: st.DrawFn) -> str:
    """Generate a class body with a handful of uniquely-named (decorated) methods."""
    count = draw(st.integers(min_value=1, max_value=6))
    names = draw(st.lists(_identifiers(), min_size=count, max_size=count, unique=True))
    lines = ["class Generated:"]
    for name in names:
        decorator = draw(st.sampled_from(_DECORATORS))
        if decorator:
            lines.append(f"    {decorator.strip()}")
        arg = "cls" if "classmethod" in decorator else ("" if "staticmethod" in decorator else "self")
        lines.append(f"    def {name}({arg}):")
        lines.append("        pass")
    return "\n".join(lines) + "\n"


def _method_names(source: str) -> list[str]:
    module = cst.parse_module(source)
    class_def = module.body[0]
    assert isinstance(class_def, cst.ClassDef)
    return [item.name.value for item in class_def.body.body if isinstance(item, cst.FunctionDef)]


def _sorted_names(source: str, groups) -> list[str]:  # noqa: ANN001
    module = cst.parse_module(source)
    class_def = module.body[0]
    assert isinstance(class_def, cst.ClassDef)
    result = _sort_block(list(class_def.body.body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
    new_class = class_def.with_changes(body=class_def.body.with_changes(body=result.new_body))
    return [item.name.value for item in new_class.body.body if isinstance(item, cst.FunctionDef)]


@given(_class_source())
def test_sorting_is_a_permutation(source: str) -> None:
    """Every method survives sorting exactly once."""
    groups = default_groups()
    assert sorted(_sorted_names(source, groups)) == sorted(_method_names(source))


@given(_class_source())
def test_sorting_is_idempotent(source: str) -> None:
    """Sorting an already-sorted body changes nothing."""
    groups = default_groups()
    once = _sorted_names(source, groups)
    # Re-emit the once-sorted source and sort again; the order must be stable.
    module = cst.parse_module(source)
    class_def = module.body[0]
    assert isinstance(class_def, cst.ClassDef)
    first = _sort_block(list(class_def.body.body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
    second = _sort_block(list(first.new_body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
    assert second.modified is False
    assert [n.name.value for n in second.new_body if isinstance(n, cst.FunctionDef)] == once


@given(_class_source())
def test_group_order_is_monotonic(source: str) -> None:
    """Matched methods appear grouped in the configured group order."""
    groups = default_groups()
    group_rank = {group.name: rank for rank, group in enumerate(groups)}
    ordered = _sorted_names(source, groups)

    def _rank(name: str) -> int:
        member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
        return group_rank[classify(member, groups).group.name]

    ranks = [_rank(name) for name in ordered]
    # Method-type is a secondary key, but group rank must be non-decreasing overall
    # because the default groups are mutually exclusive and ordering is by group first.
    assert ranks == sorted(ranks)


@given(_identifiers())
def test_default_matches_legacy_visibility(name: str) -> None:
    """classify() under default groups agrees with the independent legacy cascade."""
    member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
    result = classify(member, default_groups())
    assert result.group is not None
    assert result.group.name == _legacy_visibility(name)
    assert get_method_visibility(name) == _legacy_visibility(name)


@given(st.lists(_identifiers(), max_size=8))
def test_legacy_order_is_order_independent_membership(names: list[str]) -> None:
    """Reordering the legacy order list never changes which group a name belongs to."""
    canonical = groups_for_order(["creational", "dunder", "public", "protected", "private"])
    reordered = groups_for_order(["private", "protected", "public", "dunder", "creational"])
    for name in names:
        member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
        assert classify(member, canonical).group.name == classify(member, reordered).group.name
