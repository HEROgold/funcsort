"""Property-based tests for the sorting engine invariants."""

from __future__ import annotations

import keyword
import re

import libcst as cst
from hypothesis import given
from hypothesis import strategies as st

from funcsort.groups import (
    DEFAULT_CREATIONAL_DUNDERS,
    Group,
    Member,
    MemberKind,
    MethodKind,
    Scope,
    classify,
    default_groups,
)
from funcsort.sorter import MethodSorter, sort_block

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


@given(_identifiers())
def test_default_matches_legacy_visibility(name: str) -> None:
    """classify() under default groups agrees with the independent legacy cascade."""
    member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
    result = classify(member, default_groups())
    assert result.group is not None
    assert result.group.name == _legacy_visibility(name)


@given(st.lists(_identifiers(), max_size=8))
def test_default_membership_is_order_independent(names: list[str]) -> None:
    """The default groups are mutually exclusive: reordering never changes membership."""
    canonical = default_groups()
    reordered = list(reversed(default_groups()))
    for name in names:
        member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
        assert _group_name(member, canonical) == _group_name(member, reordered)


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
    _, body = _class_body(source)
    first = sort_block(list(body.body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
    second = sort_block(list(first.new_body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
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
        return group_rank[_group_name(member, groups)]

    ranks = [_rank(name) for name in ordered]
    # Method-type is a secondary key, but group rank must be non-decreasing overall
    # because the default groups are mutually exclusive and ordering is by group first.
    assert ranks == sorted(ranks)


def _class_body(source: str) -> tuple[cst.ClassDef, cst.IndentedBlock]:
    module = cst.parse_module(source)
    class_def = module.body[0]
    assert isinstance(class_def, cst.ClassDef)
    assert isinstance(class_def.body, cst.IndentedBlock)
    return class_def, class_def.body


def _function_names(body: cst.IndentedBlock) -> list[str]:
    return [item.name.value for item in body.body if isinstance(item, cst.FunctionDef)]


def _group_name(member: Member, groups: list[Group]) -> str:
    classification = classify(member, groups)
    assert classification.group is not None
    return classification.group.name


def _method_names(source: str) -> list[str]:
    _, body = _class_body(source)
    return _function_names(body)


def _sorted_names(source: str, groups: list[Group]) -> list[str]:
    _, body = _class_body(source)
    result = sort_block(list(body.body), scope=Scope.CLASS, groups=groups, method_type_order=_DEFAULT_MTO)
    new_body = body.with_changes(body=result.new_body)
    return _function_names(new_body)


@st.composite
def _dependent_module_source(draw: st.DrawFn) -> str:
    """Generate a module where some public functions read a protected helper eagerly."""
    count = draw(st.integers(min_value=1, max_value=4))
    lines = ["def _deco(fn):", "    return fn", ""]
    for index in range(count):
        lines += [f"def _p{index}():", "    return 1", ""]
    for index in range(count):
        if draw(st.booleans()):
            lines.append(f"@_deco(_p{index}())")
        lines += [f"def c{index}():", "    pass", ""]
    return "\n".join(lines)


@given(_dependent_module_source())
def test_load_time_dependencies_are_preserved(source: str) -> None:
    """Every generated definition still precedes the decorator that reads it."""
    text = _sorted_module(source)
    for consumer, provider in _dependencies(source):
        assert text.index(f"def {provider}(") < text.index(f"@_deco({provider}())\ndef {consumer}(")


@given(_dependent_module_source())
def test_dependency_aware_sorting_is_idempotent(source: str) -> None:
    """A dependency-repaired order must be a fixed point, or pre-commit would loop."""
    once = _sorted_module(source)
    assert _sorted_module(once) == once


def _dependencies(source: str) -> list[tuple[str, str]]:
    """Return the ``(consumer, provider)`` pairs the generated source depends on."""
    pairs = re.findall(r"@_deco\((_p\d+)\(\)\)\ndef (c\d+)\(", source)
    return [(consumer, provider) for provider, consumer in pairs]


def _sorted_module(source: str) -> str:
    """Sort ``source`` at module scope with the default groups and return the new code."""
    module = cst.parse_module(source)
    sorter = MethodSorter(default_groups(), _DEFAULT_MTO, sort_module=True)
    return module.visit(sorter).code
