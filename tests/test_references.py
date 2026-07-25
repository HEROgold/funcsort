"""Tests for eager/lazy name extraction — the analysis the ordering safety rests on."""

import libcst as cst
import pytest

from funcsort.references import NameFlow, name_flow, uses_future_annotations


def _flow(source: str, *, lazy_annotations: bool = False) -> NameFlow:
    """Return the flow of the single statement in ``source``."""
    module = cst.parse_module(source)
    assert len(module.body) == 1
    return name_flow(module.body[0], lazy_annotations=lazy_annotations)


class TestFunctionDef:
    """A function's signature runs at definition time; its body does not."""

    def test_decorator_call_arguments_are_required(self) -> None:
        # The reported regression: a name-only reading of the decorator misses _helper.
        flow = _flow("@given(_helper())\ndef test_x():\n    pass\n")
        assert flow.provides == {"test_x"}
        assert flow.requires == {"given", "_helper"}

    def test_body_is_lazy(self) -> None:
        # Without this, mutual recursion would make every block unsortable.
        flow = _flow("def _a():\n    return b() + CONST\n")
        assert flow.requires == set()

    def test_parameter_defaults_are_required(self) -> None:
        assert _flow("def pub(x=_factory, *, y=OTHER):\n    pass\n").requires == {"_factory", "OTHER"}

    def test_annotations_are_required_by_default(self) -> None:
        assert _flow("def f(a: Later) -> Also:\n    pass\n").requires == {"Later", "Also"}

    def test_lazy_annotations_drop_annotation_references(self) -> None:
        assert _flow("def f(a: Later) -> Also:\n    pass\n", lazy_annotations=True).requires == set()

    def test_string_annotations_are_always_lazy(self) -> None:
        assert _flow('def f(a: "Later") -> "Also":\n    pass\n').requires == set()

    def test_async_def_behaves_the_same(self) -> None:
        flow = _flow("@_deco\nasync def fetch(x=_default):\n    return _lazy\n")
        assert flow.provides == {"fetch"}
        assert flow.requires == {"_deco", "_default"}


class TestClassDef:
    """A class body executes at definition time, but its method bodies do not."""

    def test_bases_keywords_and_body_are_eager(self) -> None:
        flow = _flow("class C(Base, metaclass=M):\n    x = _helper()\n    def m(self):\n        return _lazy\n")
        assert flow.provides == {"C"}
        assert flow.requires == {"Base", "M", "_helper"}

    def test_decorated_class(self) -> None:
        assert _flow("@register(_key)\nclass C:\n    pass\n").requires == {"register", "_key"}


class TestAssignments:
    """Assignment targets are written; only dereferenced parts of them are read."""

    def test_simple_assignment(self) -> None:
        flow = _flow("X = _make(other)\n")
        assert flow.provides == {"X"}
        assert flow.requires == {"_make", "other"}

    def test_destructuring_binds_every_name(self) -> None:
        assert _flow("a, (b, *c) = _source()\n").provides == {"a", "b", "c"}

    def test_attribute_target_reads_its_base(self) -> None:
        flow = _flow("obj.attr = 1\n")
        assert flow.provides == set()
        assert flow.requires == {"obj"}

    def test_subscript_target_reads_base_and_key(self) -> None:
        flow = _flow("d[k] = v\n")
        assert flow.provides == set()
        assert flow.requires == {"d", "k", "v"}

    def test_bare_annotation_binds_nothing(self) -> None:
        # `x: int` is a declaration; claiming it provides `x` invents a second provider.
        assert _flow("x: int\n").provides == set()

    def test_annotated_assignment_with_value_binds(self) -> None:
        flow = _flow("x: Foo = bar()\n")
        assert flow.provides == {"x"}
        assert flow.requires == {"Foo", "bar"}

    def test_augmented_assignment_reads_and_writes(self) -> None:
        flow = _flow("total += _delta\n")
        assert flow.provides == {"total"}
        assert flow.requires == {"total", "_delta"}


class TestImports:
    """Imports bind names and read nothing."""

    @pytest.mark.parametrize(
        ("source", "provides"),
        [
            ("import a.b\n", {"a"}),
            ("import a.b as c\n", {"c"}),
            ("import os, sys\n", {"os", "sys"}),
            ("from x import y\n", {"y"}),
            ("from x import y as z\n", {"z"}),
            ("from x import *\n", set()),
        ],
    )
    def test_bound_names(self, source: str, provides: set[str]) -> None:
        flow = _flow(source)
        assert flow.provides == provides
        assert flow.requires == set()


class TestNonReferences:
    """libcst uses ``Name`` for things that are not references; none may leak through."""

    def test_attribute_chain_reads_only_its_root(self) -> None:
        assert _flow("X = mod.attr.deep\n").requires == {"mod"}

    def test_keyword_argument_name_is_not_a_reference(self) -> None:
        assert _flow("X = f(keyword=1)\n").requires == {"f"}

    def test_parameter_name_is_not_a_reference(self) -> None:
        assert _flow("def f(value):\n    pass\n").requires == set()


class TestCompoundStatements:
    """Anchored blocks both bind and read at module level."""

    def test_type_checking_block_provides_its_imports(self) -> None:
        flow = _flow("if TYPE_CHECKING:\n    from t import Thing\n    use(_helper)\n")
        assert flow.provides == {"Thing"}
        assert flow.requires == {"TYPE_CHECKING", "use", "_helper"}

    def test_try_except_provides_both_branches(self) -> None:
        source = "try:\n    import fast as impl\nexcept ImportError as exc:\n    import slow as impl\n"
        flow = _flow(source)
        assert flow.provides == {"impl", "exc"}

    def test_with_and_for_targets_bind(self) -> None:
        assert _flow("with _open() as fh:\n    pass\n").provides == {"fh"}
        assert _flow("for item in _items():\n    pass\n").provides == {"item"}


class TestExpressions:
    """Deferred expression forms."""

    def test_lambda_body_is_lazy_but_defaults_are_eager(self) -> None:
        flow = _flow("F = lambda a=_default: _lazy(a)\n")
        assert flow.provides == {"F"}
        assert flow.requires == {"_default"}

    def test_comprehension_is_fully_eager(self) -> None:
        # The comprehension-local `i` is over-collected. That is the safe direction: a
        # spurious name can only ever block a reorder, never permit a broken one.
        assert {"_transform", "_source"} <= _flow("X = [_transform(i) for i in _source]\n").requires

    def test_fstring_reads_its_expressions(self) -> None:
        assert _flow('X = f"{_value}"\n').requires == {"_value"}

    def test_type_alias_value_is_lazy(self) -> None:
        flow = _flow("type X = SomeLater\n")
        assert flow.provides == {"X"}
        assert flow.requires == set()


class TestFutureAnnotations:
    """Detection of PEP 563 deferred annotations."""

    def test_detected(self) -> None:
        assert uses_future_annotations(cst.parse_module("from __future__ import annotations\n")) is True

    def test_absent(self) -> None:
        assert uses_future_annotations(cst.parse_module("import os\n")) is False

    def test_other_future_feature_does_not_count(self) -> None:
        assert uses_future_annotations(cst.parse_module("from __future__ import division\n")) is False
