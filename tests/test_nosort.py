"""Tests for nosort comment directives."""

import tempfile
from pathlib import Path
from textwrap import dedent

from funcsort.groups import default_groups
from funcsort.sorter import SortResult, sort_file


def _sort(source: str) -> tuple[SortResult, str]:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(source)
        temp_path = Path(f.name)
    try:
        result = sort_file(temp_path, groups=default_groups(), sort_module=False)
        return result, temp_path.read_text()
    finally:
        temp_path.unlink()


class TestNosortDirectives:
    """Tests for # nosort comment functionality."""

    def test_file_level_nosort(self) -> None:
        source = dedent("""\
            # nosort: file
            class Example:
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result, text = _sort(source)
        assert result.modified is False
        assert text == source

    def test_class_level_nosort(self) -> None:
        source = dedent("""\
            class Example:  # nosort
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result, text = _sort(source)
        assert result.modified is False
        assert text == source

    def test_method_level_nosort(self) -> None:
        source = dedent("""\
            class Example:
                def public_a(self):
                    pass

                def _protected_x(self):  # nosort
                    pass

                def public_b(self):
                    pass
        """)
        result, text = _sort(source)
        assert result.modified is False
        assert text.find("def public_a") < text.find("def _protected_x") < text.find("def public_b")

    def test_multiple_nosort_methods(self) -> None:
        source = dedent("""\
            class Example:
                def public_a(self):
                    pass

                def _protected_x(self):  # nosort
                    pass

                def public_b(self):  # nosort
                    pass

                def _protected_y(self):
                    pass
        """)
        result, text = _sort(source)
        assert result.modified is False
        assert text == source

    def test_nosort_case_insensitive(self) -> None:
        source = dedent("""\
            class Example:  # NOSORT
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result, _ = _sort(source)
        assert result.modified is False

    def test_nosort_with_other_classes(self) -> None:
        source = dedent("""\
            class First:  # nosort
                def _protected(self):
                    pass

                def public(self):
                    pass

            class Second:
                def _protected(self):
                    pass

                def public(self):
                    pass
        """)
        result, text = _sort(source)
        assert result.modified is True
        first_start = text.find("class First")
        second_start = text.find("class Second")
        assert text.find("def _protected", first_start) < text.find("def public", first_start)
        assert text.find("def public", second_start) < text.find("def _protected", second_start)
