"""Tests for configuration loading."""

from pathlib import Path

import pytest

from undersort.config import _find_config_file, load_settings
from undersort.groups import Member, MemberKind, MethodKind, Scope, classify

DEFAULT_GROUP_NAMES = ["creational", "dunder", "public", "protected", "private"]


def _write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, name: str = "pyproject.toml") -> None:
    (tmp_path / name).write_text(content)
    monkeypatch.chdir(tmp_path)


def _group_names(settings) -> list[str]:  # noqa: ANN001
    return [group.name for group in settings.groups]


def _function(name: str, scope: Scope = Scope.CLASS, method_type: MethodKind = MethodKind.INSTANCE) -> Member:
    return Member(0, None, MemberKind.FUNCTION, name, method_type, scope)


class TestDefaults:
    """Defaults when no configuration is present."""

    def test_default_settings_without_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        settings = load_settings()
        assert _group_names(settings) == DEFAULT_GROUP_NAMES
        assert settings.method_type_order == [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]
        assert settings.exclude == ()
        assert settings.sort_module is True

    def test_missing_tool_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[project]\nname = 'test'\n")
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES


class TestLegacyOrder:
    """The legacy ``order`` key still drives the default groups."""

    def test_custom_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\norder = ["private", "protected", "public"]\n')
        assert _group_names(load_settings()) == ["private", "protected", "public"]

    def test_creational_and_dunder_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, f"[tool.undersort]\norder = {DEFAULT_GROUP_NAMES!r}\n")
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES

    def test_dunders_fold_into_public_when_omitted(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\norder = ["public", "protected", "private"]\n')
        settings = load_settings()
        assert _group_names(settings) == ["public", "protected", "private"]
        # A dunder name folds into "public" rather than being dropped.
        assert classify(_function("__str__"), settings.groups).group.name == "public"

    def test_invalid_order_falls_back_to_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\norder = ["public", "invalid", "private"]\n')
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES

    def test_creational_dunders_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\ncreational_dunders = ["__init__", "__enter__"]\n')
        settings = load_settings()
        assert classify(_function("__enter__"), settings.groups).group.name == "creational"
        assert classify(_function("__set_name__"), settings.groups).group.name == "dunder"


class TestGroups:
    """The new ``[[tool.undersort.groups]]`` block fully replaces the defaults."""

    def test_groups_replace_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = (
            "[tool.undersort]\n"
            "[[tool.undersort.groups]]\n"
            'name = "tests"\n'
            'match = "^test_"\n'
            "[[tool.undersort.groups]]\n"
            'name = "rest"\n'
            'match = ".*"\n'
        )
        _write(tmp_path, monkeypatch, content)
        settings = load_settings()
        assert _group_names(settings) == ["tests", "rest"]
        assert classify(_function("test_login"), settings.groups).group.name == "tests"
        assert classify(_function("helper"), settings.groups).group.name == "rest"

    def test_group_kind_and_scope_filters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = (
            "[tool.undersort]\n"
            "[[tool.undersort.groups]]\n"
            'name = "constants"\n'
            'match = "^[A-Z]"\n'
            'kind = ["assignment"]\n'
            'scope = "module"\n'
        )
        _write(tmp_path, monkeypatch, content)
        group = load_settings().groups[0]
        assert group.kinds == frozenset({MemberKind.ASSIGNMENT})
        assert group.scopes == frozenset({Scope.MODULE})

    def test_invalid_regex_falls_back_to_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = "[tool.undersort]\n[[tool.undersort.groups]]\nname = \"bad\"\nmatch = \"([unclosed\"\n"
        _write(tmp_path, monkeypatch, content)
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES

    def test_group_missing_match_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = '[tool.undersort]\n[[tool.undersort.groups]]\nname = "incomplete"\n'
        _write(tmp_path, monkeypatch, content)
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES


class TestScalarSettings:
    """Method type order, excludes and the module toggle."""

    def test_method_type_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\nmethod_type_order = ["static", "instance", "class"]\n')
        assert load_settings().method_type_order == [MethodKind.STATIC, MethodKind.INSTANCE, MethodKind.CLASS]

    def test_invalid_method_type_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\nmethod_type_order = ["instance", "bogus"]\n')
        assert load_settings().method_type_order == [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]

    def test_exclude_patterns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.undersort]\nexclude = ["tests/*", "migrations/*.py"]\n')
        assert load_settings().exclude == ("tests/*", "migrations/*.py")

    def test_sort_module_toggle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[tool.undersort]\nsort_module = false\n")
        assert load_settings().sort_module is False


class TestDiscovery:
    """Config-file discovery and precedence."""

    def test_undersort_toml_preferred_over_pyproject(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.undersort]\norder = ["public", "protected", "private"]\n')
        (tmp_path / "undersort.toml").write_text('[tool.undersort]\norder = ["private", "public"]\n')
        monkeypatch.chdir(tmp_path)
        assert _group_names(load_settings()) == ["private", "public"]

    def test_found_in_parent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        (tmp_path / "pyproject.toml").write_text('[tool.undersort]\norder = ["private", "public", "protected"]\n')
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert _group_names(load_settings()) == ["private", "public", "protected"]

    def test_find_config_file_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert _find_config_file() is None

    def test_find_config_file_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "undersort.toml"
        path.write_text("[tool.undersort]\n")
        monkeypatch.chdir(tmp_path)
        assert _find_config_file() == path

    def test_corrupted_toml_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[tool.undersort\norder = [\n")
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES
