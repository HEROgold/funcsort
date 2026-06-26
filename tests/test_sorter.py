import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

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
from funcsort.sorter import SortResult, sort_file


def _ordered(*names: str) -> list[Group]:
    """Build default groups restricted to ``names``, in the given output order."""
    by_name = {group.name: group for group in default_groups()}
    return [by_name[name] for name in names]


def _visibility(name: str, creational_dunders: tuple[str, ...] = DEFAULT_CREATIONAL_DUNDERS) -> str | None:
    member = Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, Scope.CLASS)
    classification = classify(member, default_groups(creational_dunders))
    return classification.group.name if classification.group else None


def _sort(
    source: str,
    groups: list[Group] | None = None,
    method_type_order: list[MethodKind] | None = None,
    *,
    check_only: bool = False,
) -> tuple[SortResult, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        result = sort_file(
            temp_path,
            groups=groups,
            method_type_order=method_type_order,
            sort_module=False,
            check_only=check_only,
        )
        return result, temp_path.read_text()
    finally:
        temp_path.unlink()


class TestVisibilityDetection:
    """Tests for default-group membership (the classic visibility classifier)."""

    def test_public_method(self) -> None:
        assert _visibility("method") == "public"
        assert _visibility("get_value") == "public"

    def test_creational_method(self) -> None:
        for name in DEFAULT_CREATIONAL_DUNDERS:
            assert _visibility(name) == "creational"

    def test_dunder_method(self) -> None:
        assert _visibility("__str__") == "dunder"
        assert _visibility("__repr__") == "dunder"
        assert _visibility("__eq__") == "dunder"

    def test_custom_creational_dunders(self) -> None:
        custom = ("__enter__", "__exit__")
        assert _visibility("__enter__", custom) == "creational"
        assert _visibility("__init__", custom) == "dunder"

    def test_protected_method(self) -> None:
        assert _visibility("_method") == "protected"
        assert _visibility("_internal_helper") == "protected"

    def test_private_method(self) -> None:
        assert _visibility("__method") == "private"
        assert _visibility("__private_helper") == "private"


class TestMethodSorting:
    """Tests for method sorting functionality."""

    def test_basic_sorting(self) -> None:
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def public(self):
                    pass

                def __private(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        assert text.find("def public") < text.find("def _protected") < text.find("def __private")

    def test_custom_order(self) -> None:
        source = dedent("""
            class Example:
                def public(self):
                    pass

                def __private(self):
                    pass

                def _protected(self):
                    pass
        """)
        result, text = _sort(source, _ordered("private", "protected", "public"))
        assert result.modified is True
        assert text.find("def __private") < text.find("def _protected") < text.find("def public")

    def test_already_sorted(self) -> None:
        source = dedent("""
            class Example:
                def public(self):
                    pass

                def _protected(self):
                    pass

                def __private(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is False
        assert text == source

    def test_decorators_preserved(self) -> None:
        source = dedent("""
            class Example:
                @property
                def _protected_prop(self):
                    pass

                @staticmethod
                def public_static():
                    pass

                def __private(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        assert text.find("def public_static") < text.find("def _protected_prop") < text.find("def __private")
        assert "@staticmethod" in text
        assert "@property" in text

    def test_class_variables_preserved(self) -> None:
        source = dedent("""
            class Example:
                class_var = "value"

                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        expected = dedent("""
            class Example:
                class_var = "value"

                def public(self):
                    pass

                def _protected(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        assert text == expected

    def test_empty_class(self) -> None:
        source = dedent("""
            class Empty:
                pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is False
        assert text == source

    def test_multiple_classes(self) -> None:
        source = dedent("""
            class First:
                def _protected(self):
                    pass

                def public(self):
                    pass

            class Second:
                def __private(self):
                    pass

                def public(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        first = text[text.find("class First:") : text.find("class Second:")]
        second = text[text.find("class Second:") :]
        assert first.find("def public") < first.find("def _protected")
        assert second.find("def public") < second.find("def __private")

    def test_check_mode(self) -> None:
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"), check_only=True)
        assert result.modified is True
        assert text == source

    def test_syntax_error(self) -> None:
        source = "class Example:\n    def method(self)\n        pass\n"
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)
        try:
            with pytest.raises(ValueError, match="Syntax error"):
                sort_file(temp_path, groups=default_groups())
        finally:
            temp_path.unlink()


class TestDunderSorting:
    """Tests for creational/dunder grouping with the default groups."""

    def test_creational_before_classmethod(self) -> None:
        source = dedent("""
            class Example:
                @classmethod
                def default(cls):
                    pass

                def __init__(self):
                    pass
        """)
        result, text = _sort(source, default_groups())
        assert result.modified is True
        assert text.find("def __init__") < text.find("def default")

    def test_dunders_grouped_at_front(self) -> None:
        source = dedent("""
            class Example:
                def __init__(self):
                    pass

                def random_public(self):
                    pass

                def __str__(self):
                    pass
        """)
        result, text = _sort(source, default_groups())
        assert result.modified is True
        assert text.find("def __init__") < text.find("def __str__") < text.find("def random_public")

    def test_dunder_before_creational_when_reordered(self) -> None:
        source = dedent("""
            class Example:
                def __init__(self):
                    pass

                def __str__(self):
                    pass
        """)
        result, text = _sort(source, _ordered("dunder", "creational", "public", "protected", "private"))
        assert result.modified is True
        assert text.find("def __str__") < text.find("def __init__")

    def test_custom_creational_list(self) -> None:
        source = dedent("""
            class Example:
                def __str__(self):
                    pass

                def __enter__(self):
                    pass
        """)
        groups = default_groups(("__enter__",))
        result, text = _sort(source, groups)
        assert result.modified is True
        assert text.find("def __enter__") < text.find("def __str__")


class TestMethodTypeSorting:
    """Tests for method type sorting (class, static, instance)."""

    def test_method_type_sorting_default_order(self) -> None:
        source = dedent("""
            class Example:
                def instance_method(self):
                    pass

                @staticmethod
                def static_method():
                    pass

                @classmethod
                def class_method(cls):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        assert text.find("def instance_method") < text.find("def class_method") < text.find("def static_method")

    def test_method_type_sorting_custom_order(self) -> None:
        source = dedent("""
            class Example:
                def instance_method(self):
                    pass

                @classmethod
                def class_method(cls):
                    pass

                @staticmethod
                def static_method():
                    pass
        """)
        result, text = _sort(
            source,
            _ordered("public", "protected", "private"),
            method_type_order=[MethodKind.CLASS, MethodKind.STATIC, MethodKind.INSTANCE],
        )
        assert result.modified is True
        assert text.find("def class_method") < text.find("def static_method") < text.find("def instance_method")

    def test_combined_visibility_and_method_type_sorting(self) -> None:
        source = dedent("""
            class Example:
                def _protected_instance(self):
                    pass

                @classmethod
                def public_class(cls):
                    pass

                @staticmethod
                def _protected_static():
                    pass

                def public_instance(self):
                    pass

                @classmethod
                def _protected_class(cls):
                    pass
        """)
        result, text = _sort(source, _ordered("public", "protected", "private"))
        assert result.modified is True
        assert text.find("def public_instance") < text.find("def _protected_instance")
        assert text.find("def public_class") < text.find("def _protected_class")
        assert text.find("def public_instance") < text.find("def public_class")
        prot_instance = text.find("def _protected_instance")
        prot_class = text.find("def _protected_class")
        prot_static = text.find("def _protected_static")
        assert prot_instance < prot_class < prot_static
