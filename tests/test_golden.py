"""Golden test: the default configuration produces a fixed, exact output."""

import tempfile
from pathlib import Path
from textwrap import dedent

from undersort.groups import default_groups
from undersort.sorter import sort_file

_INPUT = dedent('''\
    class Service:
        URL = "https://example.com"

        def __repr__(self):
            return "Service"

        @staticmethod
        def helper():
            pass

        def _validate(self):
            pass

        def __init__(self, x):
            self.x = x

        @classmethod
        def create(cls):
            return cls(0)

        def run(self):
            pass

        def __private(self):
            pass
''')

# Canonical default ordering: an anchored class constant, then
# creational -> dunder -> public (instance, class, static) -> protected -> private.
_EXPECTED = dedent('''\
    class Service:
        URL = "https://example.com"

        def __init__(self, x):
            self.x = x

        def __repr__(self):
            return "Service"

        def run(self):
            pass

        @classmethod
        def create(cls):
            return cls(0)

        @staticmethod
        def helper():
            pass

        def _validate(self):
            pass

        def __private(self):
            pass
''')


def test_default_config_golden_output() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_INPUT)
        temp_path = Path(f.name)
    try:
        result = sort_file(temp_path, groups=default_groups(), sort_module=False)
        assert result.modified is True
        assert temp_path.read_text() == _EXPECTED
    finally:
        temp_path.unlink()


def test_default_config_is_idempotent_on_golden() -> None:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(_EXPECTED)
        temp_path = Path(f.name)
    try:
        result = sort_file(temp_path, groups=default_groups(), sort_module=False)
        assert result.modified is False
        assert temp_path.read_text() == _EXPECTED
    finally:
        temp_path.unlink()
