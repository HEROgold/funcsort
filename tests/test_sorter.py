import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from undersort.sorter import DEFAULT_CREATIONAL_DUNDERS, get_method_visibility, sort_file


class TestVisibilityDetection:
    """Tests for method visibility detection."""

    def test_public_method(self) -> None:
        """Test detection of public methods."""
        assert get_method_visibility("method") == "public"
        assert get_method_visibility("get_value") == "public"

    def test_creational_method(self) -> None:
        """Test that creational dunders are detected as 'creational'."""
        assert get_method_visibility("__init__") == "creational"
        assert get_method_visibility("__new__") == "creational"
        assert get_method_visibility("__init_subclass__") == "creational"
        assert get_method_visibility("__post_init__") == "creational"
        assert get_method_visibility("__set_name__") == "creational"
        # Every default creational name classifies as creational.
        for name in DEFAULT_CREATIONAL_DUNDERS:
            assert get_method_visibility(name) == "creational"

    def test_dunder_method(self) -> None:
        """Test that non-creational magic methods are detected as 'dunder'."""
        assert get_method_visibility("__str__") == "dunder"
        assert get_method_visibility("__repr__") == "dunder"
        assert get_method_visibility("__eq__") == "dunder"
        assert get_method_visibility("__get__") == "dunder"
        assert get_method_visibility("__set__") == "dunder"

    def test_custom_creational_dunders(self) -> None:
        """Test that the creational set can be overridden."""
        custom = ["__enter__", "__exit__"]
        assert get_method_visibility("__enter__", custom) == "creational"
        assert get_method_visibility("__exit__", custom) == "creational"
        # __init__ is no longer creational when overridden out of the set.
        assert get_method_visibility("__init__", custom) == "dunder"

    def test_protected_method(self) -> None:
        """Test detection of protected methods."""
        assert get_method_visibility("_method") == "protected"
        assert get_method_visibility("_get_value") == "protected"
        assert get_method_visibility("_internal_helper") == "protected"

    def test_private_method(self) -> None:
        """Test detection of private methods."""
        assert get_method_visibility("__method") == "private"
        assert get_method_visibility("__private_helper") == "private"


class TestMethodSorting:
    """Tests for method sorting functionality."""

    def test_basic_sorting(self) -> None:
        """Test basic method sorting with default order."""
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def public(self):
                    pass

                def __private(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            public_idx = result.find("def public")
            protected_idx = result.find("def _protected")
            private_idx = result.find("def __private")

            assert public_idx < protected_idx < private_idx
        finally:
            temp_path.unlink()

    def test_custom_order(self) -> None:
        """Test sorting with custom order (private first)."""
        source = dedent("""
            class Example:
                def public(self):
                    pass

                def __private(self):
                    pass

                def _protected(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["private", "protected", "public"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            private_idx = result.find("def __private")
            protected_idx = result.find("def _protected")
            public_idx = result.find("def public")

            assert private_idx < protected_idx < public_idx
        finally:
            temp_path.unlink()

    def test_already_sorted(self) -> None:
        """Test that already sorted code is not modified."""
        source = dedent("""
            class Example:
                def public(self):
                    pass

                def _protected(self):
                    pass

                def __private(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is False

            with open(temp_path) as f:
                result = f.read()

            assert result == source
        finally:
            temp_path.unlink()

    def test_decorators_preserved(self) -> None:
        """Test that decorators are preserved during sorting."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            public_static_idx = result.find("def public_static")
            protected_prop_idx = result.find("def _protected_prop")
            private_idx = result.find("def __private")

            assert public_static_idx < protected_prop_idx < private_idx

            assert "@staticmethod" in result
            assert "@property" in result
        finally:
            temp_path.unlink()

    def test_class_variables_preserved(self) -> None:
        """Test that class variables remain at the top."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            assert result == expected
        finally:
            temp_path.unlink()

    def test_empty_class(self) -> None:
        """Test that empty classes are not modified."""
        source = dedent("""
            class Empty:
                pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is False

            with open(temp_path) as f:
                result = f.read()

            assert result == source
        finally:
            temp_path.unlink()

    def test_multiple_classes(self) -> None:
        """Test sorting multiple classes in one file."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            first_class_start = result.find("class First:")
            second_class_start = result.find("class Second:")

            # Extract methods from First class
            first_class_methods = result[first_class_start:second_class_start]
            first_public_idx = first_class_methods.find("def public")
            first_protected_idx = first_class_methods.find("def _protected")
            assert first_public_idx < first_protected_idx

            # Extract methods from Second class
            second_class_methods = result[second_class_start:]
            second_public_idx = second_class_methods.find("def public")
            second_private_idx = second_class_methods.find("def __private")
            assert second_public_idx < second_private_idx
        finally:
            temp_path.unlink()

    def test_check_mode(self) -> None:
        """Test check mode does not modify files."""
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order, check_only=True)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            assert result == source
        finally:
            temp_path.unlink()

    def test_syntax_error(self) -> None:
        """Test that syntax errors are properly reported."""
        source = dedent("""
            class Example:
                def method(self)
                    pass  # Missing colon
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            with pytest.raises(ValueError, match="Syntax error"):
                sort_file(temp_path, order)
        finally:
            temp_path.unlink()

    def test_init_stays_with_public(self) -> None:
        """Test that __init__ stays with public methods."""
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def __init__(self):
                    pass

                def public(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            was_modified = sort_file(temp_path, order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            init_idx = result.find("def __init__")
            protected_idx = result.find("def _protected")
            assert init_idx < protected_idx

            public_idx = result.find("def public")
            assert public_idx < protected_idx
        finally:
            temp_path.unlink()


class TestDunderSorting:
    """Tests for the creational/dunder grouping feature (issue #1)."""

    DEFAULT_ORDER = ["creational", "dunder", "public", "protected", "private"]

    def test_creational_before_classmethod(self) -> None:
        """Issue example 1: __init__ should sort before a public classmethod."""
        source = dedent("""
            class Example:
                @classmethod
                def default(cls):
                    pass

                def __init__(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            was_modified = sort_file(temp_path, self.DEFAULT_ORDER)
            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            init_idx = result.find("def __init__")
            default_idx = result.find("def default")
            assert init_idx < default_idx
        finally:
            temp_path.unlink()

    def test_dunders_grouped_at_front(self) -> None:
        """Issue example 2: __init__ and __str__ group together ahead of public methods."""
        source = dedent("""
            class Example:
                def __init__(self):
                    pass

                def random_public(self):
                    pass

                def __str__(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            was_modified = sort_file(temp_path, self.DEFAULT_ORDER)
            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            init_idx = result.find("def __init__")
            str_idx = result.find("def __str__")
            random_idx = result.find("def random_public")

            # creational (__init__) -> dunder (__str__) -> public (random_public)
            assert init_idx < str_idx < random_idx
        finally:
            temp_path.unlink()

    def test_dunder_before_creational_when_reordered(self) -> None:
        """The two groups are independently orderable via `order`."""
        source = dedent("""
            class Example:
                def __init__(self):
                    pass

                def __str__(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["dunder", "creational", "public", "protected", "private"]
            was_modified = sort_file(temp_path, order)
            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            str_idx = result.find("def __str__")
            init_idx = result.find("def __init__")
            assert str_idx < init_idx
        finally:
            temp_path.unlink()

    def test_backward_compat_dunders_fall_back_to_public(self) -> None:
        """When order omits creational/dunder, those methods behave as public (not dropped)."""
        source = dedent("""
            class Example:
                def _protected(self):
                    pass

                def __init__(self):
                    pass

                def public(self):
                    pass

                def __str__(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            sort_file(temp_path, order)

            with open(temp_path) as f:
                result = f.read()

            # Dunders must not be dropped.
            assert "def __init__" in result
            assert "def __str__" in result

            init_idx = result.find("def __init__")
            str_idx = result.find("def __str__")
            protected_idx = result.find("def _protected")
            # Grouped with public => ahead of protected.
            assert init_idx < protected_idx
            assert str_idx < protected_idx
        finally:
            temp_path.unlink()

    def test_custom_creational_list(self) -> None:
        """A custom creational list reclassifies which dunders are creational."""
        source = dedent("""
            class Example:
                def __str__(self):
                    pass

                def __enter__(self):
                    pass
        """)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["creational", "dunder", "public", "protected", "private"]
            # __enter__ becomes creational, __str__ remains a generic dunder.
            was_modified = sort_file(temp_path, order, creational_dunders=["__enter__"])
            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            enter_idx = result.find("def __enter__")
            str_idx = result.find("def __str__")
            assert enter_idx < str_idx
        finally:
            temp_path.unlink()


class TestMethodTypeSorting:
    """Tests for method type sorting (class, static, instance)."""

    def test_method_type_sorting_default_order(self) -> None:
        """Test that methods are sorted by type within visibility levels."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            method_type_order = ["instance", "class", "static"]
            was_modified = sort_file(temp_path, order, method_type_order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            instance_idx = result.find("def instance_method")
            class_idx = result.find("def class_method")
            static_idx = result.find("def static_method")

            assert instance_idx < class_idx < static_idx
        finally:
            temp_path.unlink()

    def test_method_type_sorting_custom_order(self) -> None:
        """Test custom method type ordering (class first)."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            method_type_order = ["class", "static", "instance"]
            was_modified = sort_file(temp_path, order, method_type_order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            class_idx = result.find("def class_method")
            static_idx = result.find("def static_method")
            instance_idx = result.find("def instance_method")

            assert class_idx < static_idx < instance_idx
        finally:
            temp_path.unlink()

    def test_combined_visibility_and_method_type_sorting(self) -> None:
        """Test sorting by both visibility and method type."""
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
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(source)
            temp_path = Path(f.name)

        try:
            order = ["public", "protected", "private"]
            method_type_order = ["instance", "class", "static"]
            was_modified = sort_file(temp_path, order, method_type_order)

            assert was_modified.modified is True

            with open(temp_path) as f:
                result = f.read()

            public_instance_idx = result.find("def public_instance")
            public_class_idx = result.find("def public_class")

            protected_instance_idx = result.find("def _protected_instance")
            protected_class_idx = result.find("def _protected_class")
            protected_static_idx = result.find("def _protected_static")

            assert public_instance_idx < protected_instance_idx
            assert public_class_idx < protected_class_idx

            assert public_instance_idx < public_class_idx

            assert protected_instance_idx < protected_class_idx < protected_static_idx
        finally:
            temp_path.unlink()
