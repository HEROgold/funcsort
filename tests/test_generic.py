"""Tests for the generic engine: regex groups, scope/kind filters, module sorting."""

import tempfile
from pathlib import Path
from textwrap import dedent

from undersort.groups import (
    Group,
    MemberKind,
    Scope,
    compile_matcher,
    default_groups,
    groups_for_order,
)
from undersort.sorter import sort_file


def _sort(source: str, **kwargs) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        sort_file(temp_path, **kwargs)
        return temp_path.read_text()
    finally:
        temp_path.unlink()


def _group(name: str, *matchers: str, **kwargs) -> Group:
    return Group(name, tuple(compile_matcher(m) for m in matchers), **kwargs)


class TestRegexGroups:
    """Custom regex groups with first-match-wins ordering."""

    def test_first_match_wins(self) -> None:
        source = dedent("""
            class Example:
                def helper(self):
                    pass

                def test_login(self):
                    pass
        """)
        groups = [_group("tests", "^test_"), _group("rest", ".*")]
        result = _sort(source, groups=groups, sort_module=False)
        assert result.find("def test_login") < result.find("def helper")

    def test_unmatched_moves_to_end_and_is_reported(self) -> None:
        source = dedent("""
            class Example:
                def keep_me(self):
                    pass

                def test_a(self):
                    pass
        """)
        # Only test_* matches; keep_me is unmatched and should move to the end.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)
        try:
            result = sort_file(temp_path, groups=[_group("tests", "^test_")], sort_module=False)
            text = temp_path.read_text()
            assert text.find("def test_a") < text.find("def keep_me")
            assert {m.name for m in result.unmatched} == {"keep_me"}
        finally:
            temp_path.unlink()


class TestScopeAndKindFilters:
    """Groups can target a scope or member kind."""

    def test_assignment_group_sorts_constants(self) -> None:
        source = dedent("""
            class Example:
                def method(self):
                    pass

                ZeBRA = 1
                alpha = 2
        """)
        groups = [
            _group("constants", "^[A-Za-z]", kinds=frozenset({MemberKind.ASSIGNMENT})),
            *default_groups(),
        ]
        result = _sort(source, groups=groups, sort_module=False)
        # Assignments are now sortable and move ahead of the method.
        assert result.find("ZeBRA = 1") < result.find("def method")
        assert result.find("alpha = 2") < result.find("def method")

    def test_default_leaves_assignments_anchored(self) -> None:
        source = dedent("""
            class Example:
                CONST = 1
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result = _sort(source, groups=default_groups(), sort_module=False)
        # CONST stays on top (no group targets assignments by default); methods sort.
        assert result.index("CONST = 1") < result.index("def public")
        assert result.index("def public") < result.index("def _protected")

    def test_scope_filter_restricts_to_class(self) -> None:
        source = dedent("""
            def zzz():
                pass

            def aaa():
                pass
        """)
        # A class-only group never matches module functions, so they are unmatched.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)
        try:
            result = sort_file(
                temp_path,
                groups=[_group("classonly", ".*", scopes=frozenset({Scope.CLASS}))],
                sort_module=True,
            )
            assert {m.name for m in result.unmatched} == {"zzz", "aaa"}
        finally:
            temp_path.unlink()


class TestModuleSorting:
    """Module-level functions are sorted when enabled."""

    def test_module_functions_sorted_by_default(self) -> None:
        source = dedent("""
            def _helper():
                pass

            def public_fn():
                pass
        """)
        result = _sort(source, groups=default_groups(), sort_module=True)
        assert result.index("def public_fn") < result.index("def _helper")

    def test_module_sorting_disabled(self) -> None:
        source = dedent("""
            def _helper():
                pass

            def public_fn():
                pass
        """)
        result = _sort(source, groups=default_groups(), sort_module=False)
        assert result.index("def _helper") < result.index("def public_fn")

    def test_module_imports_and_dunder_all_anchored(self) -> None:
        source = dedent("""
            import os

            __all__ = ["public_fn"]

            def _helper():
                pass

            def public_fn():
                pass
        """)
        result = _sort(source, groups=default_groups(), sort_module=True)
        assert result.index("import os") < result.index("__all__")
        assert result.index("__all__") < result.index("def public_fn")
        assert result.index("def public_fn") < result.index("def _helper")


class TestAsyncAndDecorators:
    """Async functions and decorated methods sort like their sync peers."""

    def test_async_functions_sorted(self) -> None:
        source = dedent("""
            class Example:
                async def _b(self):
                    pass

                async def a(self):
                    pass
        """)
        result = _sort(source, groups=groups_for_order(["public", "protected", "private"]), sort_module=False)
        assert result.index("async def a") < result.index("async def _b")

    def test_cached_property_treated_as_instance(self) -> None:
        source = dedent("""
            import functools

            class Example:
                @functools.cached_property
                def _value(self):
                    pass

                def public(self):
                    pass
        """)
        result = _sort(source, groups=groups_for_order(["public", "protected", "private"]), sort_module=True)
        assert result.index("def public") < result.index("def _value")
