"""Where module-level functions land relative to module-level classes."""

import inspect
import tempfile
from pathlib import Path
from textwrap import dedent

from funcsort.config import Settings
from funcsort.groups import FunctionPlacement
from funcsort.sorter import SortResult, sort_file

from .importability import assert_resolves

# The layout from the issue: a public and a private function around a class.
_MIXED = dedent("""
    import os


    def func1():
        pass


    class Example:
        def method(self):
            pass


    def _func2():
        pass
""")


def _sort(
    source: str,
    placement: FunctionPlacement = FunctionPlacement.AFTER_CLASSES,
    *,
    respect_dependencies: bool = True,
) -> tuple[SortResult, str]:
    """Sort ``source`` as a module and return the result object and the new text."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        result = sort_file(
            temp_path,
            sort_module=True,
            respect_dependencies=respect_dependencies,
            function_placement=placement,
        )
        return result, temp_path.read_text()
    finally:
        temp_path.unlink()


def _order(text: str, *needles: str) -> list[int]:
    """Return the positions of ``needles`` in ``text``, asserting each appears once."""
    for needle in needles:
        assert text.count(needle) == 1, f"{needle!r} appears {text.count(needle)} times"
    return [text.index(needle) for needle in needles]


class TestAfterClasses:
    """The default: every module function sits below every module class."""

    def test_public_and_private_functions_both_go_below_the_class(self) -> None:
        _, text = _sort(_MIXED)
        klass, public, private = _order(text, "class Example", "def func1", "def _func2")
        assert klass < public < private

    def test_is_the_default(self) -> None:
        assert inspect.signature(sort_file).parameters["function_placement"].default is FunctionPlacement.AFTER_CLASSES
        assert Settings().function_placement is FunctionPlacement.AFTER_CLASSES

    def test_functions_keep_group_order_below_several_classes(self) -> None:
        source = dedent("""
            def _private():
                pass

            class A:
                pass

            def public():
                pass

            class B(A):
                pass
        """)
        _, text = _sort(source)
        a, b, public, private = _order(text, "class A", "class B", "def public", "def _private")
        assert a < b < public < private

    def test_anchors_stay_put(self) -> None:
        _, text = _sort(_MIXED)
        assert text.index("import os") < text.index("class Example")

    def test_second_pass_changes_nothing(self) -> None:
        _, once = _sort(_MIXED)
        result, twice = _sort(once)
        assert result.modified is False
        assert twice == once

    def test_already_grouped_module_is_untouched(self) -> None:
        source = dedent("""
            class Example:
                pass

            def public():
                pass

            def _private():
                pass
        """)
        result, text = _sort(source)
        assert result.modified is False
        assert text == source

    def test_nosort_class_stays_in_place(self) -> None:
        source = dedent("""
            def public():
                pass

            class Pinned:  # nosort
                pass
        """)
        result, _ = _sort(source)
        assert result.modified is False

    def test_class_scope_is_unaffected(self) -> None:
        source = dedent("""
            class Outer:
                def method(self):
                    pass

                class Inner:
                    pass
        """)
        result, _ = _sort(source)
        assert result.modified is False


class TestBeforeClasses:
    """Every module function sits above every module class."""

    def test_functions_go_above_the_class(self) -> None:
        _, text = _sort(_MIXED, FunctionPlacement.BEFORE_CLASSES)
        public, private, klass = _order(text, "def func1", "def _func2", "class Example")
        assert public < private < klass


class TestInterleaved:
    """The legacy layout: classes stay anchored and functions fill the gaps in group order."""

    def test_public_rises_above_and_private_sinks_below(self) -> None:
        result, text = _sort(_MIXED, FunctionPlacement.INTERLEAVED)
        assert result.modified is False
        public, klass, private = _order(text, "def func1", "class Example", "def _func2")
        assert public < klass < private


class TestDependencies:
    """Placement is a preference; load-time dependencies still win."""

    _DECORATED = dedent("""
        def _register(cls):
            return cls


        def public():
            pass


        @_register
        class Example:
            pass
    """)

    def test_class_decorator_keeps_its_function_above(self, tmp_path: Path) -> None:
        _, text = _sort(self._DECORATED)
        register, klass, public = _order(text, "def _register", "class Example", "def public")
        assert register < klass < public
        assert_resolves(text, tmp_path)

    def test_repaired_order_is_a_fixed_point(self) -> None:
        _, once = _sort(self._DECORATED)
        result, twice = _sort(once)
        assert result.modified is False
        assert twice == once

    def test_class_body_call_keeps_its_function_above(self) -> None:
        source = dedent("""
            def _default():
                return 1

            class Example:
                value = _default()

            def public():
                pass
        """)
        _, text = _sort(source)
        default, klass, public = _order(text, "def _default", "class Example", "def public")
        assert default < klass < public

    def test_function_reading_a_class_keeps_the_class_above(self) -> None:
        source = dedent("""
            class Base:
                pass

            def public(kind=Base):
                pass
        """)
        _, text = _sort(source, FunctionPlacement.BEFORE_CLASSES)
        base, public = _order(text, "class Base", "def public")
        assert base < public

    def test_disabling_dependencies_moves_the_decorator_below(self) -> None:
        _, text = _sort(self._DECORATED, respect_dependencies=False)
        klass, public, register = _order(text, "class Example", "def public", "def _register")
        assert klass < public < register
