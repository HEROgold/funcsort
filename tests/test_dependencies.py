"""End-to-end tests that sorting never breaks load-time name resolution.

Reordering is only safe if every name a statement reads *while it executes* is already
bound. These tests drive the whole pipeline — parse, classify, extract, solve, emit — and
several of them hand the sorted output to :mod:`tests.importability`, which proves it
still resolves every name without ever running it.
"""

import re
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from funcsort.groups import Group, MemberKind, compile_matcher, default_groups
from funcsort.sorter import SortResult, sort_file

from .importability import CHECKERS, assert_resolves


def _sort(source: str, *, respect_dependencies: bool = True, groups: list[Group] | None = None) -> str:
    """Sort ``source`` as a module and return the result."""
    return _sort_result(source, respect_dependencies=respect_dependencies, groups=groups)[1]


def _sort_result(
    source: str,
    *,
    respect_dependencies: bool = True,
    groups: list[Group] | None = None,
) -> tuple[SortResult, str]:
    """Sort ``source`` as a module and return the result object and the new text."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        result = sort_file(
            temp_path,
            groups=groups if groups is not None else default_groups(),
            sort_module=True,
            respect_dependencies=respect_dependencies,
        )
        return result, temp_path.read_text()
    finally:
        temp_path.unlink()


def _order(text: str, *needles: str) -> list[int]:
    """Return the positions of ``needles`` in ``text``, asserting each appears once."""
    positions = []
    for needle in needles:
        assert text.count(needle) == 1, f"{needle!r} appears {text.count(needle)} times"
        positions.append(text.index(needle))
    return positions


class TestDecoratorDependencies:
    """The reported regression: a decorator argument referencing a later definition."""

    def test_decorator_argument_keeps_helper_first(self) -> None:
        source = dedent("""
            def _helper():
                return 1

            @deco(_helper())
            def test_thing():
                pass
        """)
        text = _sort(source)
        helper, test = _order(text, "def _helper", "def test_thing")
        assert helper < test, "public test_thing must not be hoisted above the helper it calls"

    def test_bare_decorator_name_keeps_decorator_first(self) -> None:
        source = dedent("""
            def _register(fn):
                return fn

            @_register
            def public():
                pass
        """)
        registrar, public = _order(_sort(source), "def _register", "def public")
        assert registrar < public

    def test_sorted_output_still_imports(self, tmp_path: Path) -> None:
        source = dedent("""
            def _make(value):
                return lambda: value

            @property
            def _unused():
                pass

            def public(callback=_make(1)):
                return callback()
        """)
        assert_resolves(_sort(source), tmp_path)


class TestArgumentAndAnnotationDependencies:
    """Signatures are evaluated at definition time."""

    def test_default_argument_keeps_factory_first(self) -> None:
        source = dedent("""
            def _default():
                return 1

            def public(value=_default()):
                return value
        """)
        default, public = _order(_sort(source), "def _default", "def public")
        assert default < public

    def test_annotation_constrains_without_future_import(self) -> None:
        source = dedent("""
            def _Kind():
                return int

            def public(value: _Kind):
                return value
        """)
        kind, public = _order(_sort(source), "def _Kind", "def public")
        assert kind < public

    def test_future_annotations_leave_ordering_free(self) -> None:
        source = dedent("""
            from __future__ import annotations


            def _Kind():
                return int

            def public(value: _Kind):
                return value
        """)
        # PEP 563 defers the annotation, so nothing pins the order and grouping wins.
        kind, public = _order(_sort(source), "def _Kind", "def public")
        assert public < kind

    def test_string_annotation_leaves_ordering_free(self) -> None:
        source = dedent("""
            def _Kind():
                return int

            def public(value: "_Kind"):
                return value
        """)
        kind, public = _order(_sort(source), "def _Kind", "def public")
        assert public < kind


class TestLazyReferencesStillSort:
    """Function bodies run later, so they must never constrain the order."""

    def test_mutual_recursion_sorts_freely(self) -> None:
        source = dedent("""
            def _a(n):
                return b(n)

            def b(n):
                return _a(n)
        """)
        # If bodies created edges, this pair would deadlock and never sort.
        a, b = _order(_sort(source), "def _a", "def b")
        assert b < a

    def test_body_reference_does_not_pin(self) -> None:
        source = dedent("""
            def _helper():
                return 1

            def public():
                return _helper()
        """)
        helper, public = _order(_sort(source), "def _helper", "def public")
        assert public < helper


class TestAnchorDependencies:
    """Immovable statements impose release times and deadlines."""

    def test_deadline_keeps_definition_above_its_consumer(self, tmp_path: Path) -> None:
        source = dedent("""
            def _make():
                return 1

            REGISTRY = _make()

            def public():
                pass
        """)
        # REGISTRY is an anchored assignment that calls _make, so _make cannot sink below it.
        text = _sort(source)
        make, registry = _order(text, "def _make", "REGISTRY = _make()")
        assert make < registry
        assert_resolves(text, tmp_path)

    def test_release_time_keeps_reader_below_its_anchor(self, tmp_path: Path) -> None:
        source = dedent("""
            def _first():
                pass

            CONST = 5

            def public(value=CONST):
                return value
        """)
        # `public` would normally hoist to the top, but that is above the anchored CONST
        # it reads, so it has to stay in the later slot.
        text = _sort(source)
        const, public = _order(text, "CONST = 5", "def public")
        assert const < public
        assert_resolves(text, tmp_path)

    def test_class_body_reference_is_eager(self, tmp_path: Path) -> None:
        source = dedent("""
            def _helper():
                return 1

            class Holder:
                value = _helper()

            def public():
                pass
        """)
        text = _sort(source)
        helper, holder = _order(text, "def _helper", "class Holder")
        assert helper < holder
        assert_resolves(text, tmp_path)


class TestClassScope:
    """Class bodies execute top to bottom, so the same rules apply inside them."""

    def test_property_assignment_keeps_getter_first(self, tmp_path: Path) -> None:
        source = dedent("""
            class Example:
                def _getter(self):
                    return 1

                value = property(_getter)

                def public(self):
                    pass
        """)
        text = _sort(source)
        getter, value = _order(text, "def _getter", "value = property(_getter)")
        assert getter < value
        assert_resolves(text, tmp_path)

    def test_sortable_class_attribute_stays_after_its_source(self) -> None:
        groups = [
            Group("constants", (compile_matcher("^[A-Z]"),), kinds=frozenset({MemberKind.ASSIGNMENT})),
            *default_groups(),
        ]
        source = dedent("""
            class Example:
                def _build(self):
                    pass

                SIZE = len("abc")

                def public(self):
                    pass
        """)
        # SIZE is sortable here and hoists above the methods; it depends on nothing.
        text = _sort(source, groups=groups)
        assert text.index("SIZE = ") < text.index("def public")


class TestEscapeHatch:
    """``respect_dependencies=False`` restores pure group ordering."""

    def test_disabled_reproduces_the_unsafe_order(self) -> None:
        source = dedent("""
            def _helper():
                return 1

            @deco(_helper())
            def test_thing():
                pass
        """)
        text = _sort(source, respect_dependencies=False)
        helper, test = _order(text, "def _helper", "def test_thing")
        assert test < helper, "the flag must actually turn the analysis off"

    def test_enabled_is_the_default(self) -> None:
        source = dedent("""
            def _helper():
                return 1

            @deco(_helper())
            def test_thing():
                pass
        """)
        assert _sort(source) != _sort(source, respect_dependencies=False)


class TestIdempotency:
    """A repaired order must be a fixed point, or pre-commit would rewrite forever."""

    @pytest.mark.parametrize(
        "source",
        [
            dedent("""
                def _helper():
                    return 1

                @deco(_helper())
                def test_thing():
                    pass
            """),
            dedent("""
                def _make():
                    return 1

                REGISTRY = _make()

                def public():
                    pass
            """),
            dedent("""
                class Example:
                    def _getter(self):
                        return 1

                    value = property(_getter)

                    def public(self):
                        pass
            """),
        ],
    )
    def test_second_pass_changes_nothing(self, source: str) -> None:
        once = _sort(source)
        result, twice = _sort_result(once)
        assert result.modified is False
        assert twice == once


class TestSelfHosting:
    """funcsort must be stable on its own property-test module — the reported bug."""

    def test_property_test_module_survives_sorting(self, tmp_path: Path) -> None:
        source = (Path(__file__).parent / "test_properties.py").read_text(encoding="utf-8")
        text = _sort(source)

        # Every strategy helper must still precede the @given decorators that call it.
        for helper in ("_identifiers", "_class_source"):
            definition = text.index(f"def {helper}(")
            first_use = min(match.start() for match in re.finditer(rf"@given\(.*{helper}\(", text))
            assert definition < first_use, f"{helper} was hoisted below its @given use"

        assert_resolves(text, tmp_path)


class TestTheCheckerItself:
    """The name checker has to be able to fail, or every assertion above is vacuous."""

    def test_use_before_definition_is_rejected(self, tmp_path: Path) -> None:
        unsafe = dedent("""
            VALUE = _make()


            def _make():
                return 1
        """)
        with pytest.raises(AssertionError, match="does not resolve"):
            assert_resolves(unsafe, tmp_path)

    def test_an_order_aware_checker_is_installed(self) -> None:
        installed = [checker for checker in CHECKERS if checker.order_aware and checker.executable is not None]
        assert installed, "no order-aware checker is installed, so use-before-definition would go unnoticed"
