"""Tests for configuration loading."""

from pathlib import Path

import pytest

from funcsort.config import Settings, find_config_file, load_settings
from funcsort.groups import Group, Member, MemberKind, MethodKind, Scope, classify

DEFAULT_GROUP_NAMES = ["creational", "dunder", "public", "protected", "private"]


def _write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, content: str, name: str = "pyproject.toml") -> None:
    (tmp_path / name).write_text(content)
    monkeypatch.chdir(tmp_path)


def _group_names(settings: Settings) -> list[str]:
    return [group.name for group in settings.groups]


def _function(name: str, scope: Scope = Scope.CLASS, decorators: tuple[str, ...] = ()) -> Member:
    return Member(0, None, MemberKind.FUNCTION, name, MethodKind.INSTANCE, scope, decorators)


def _group_for(member: Member, groups: list[Group]) -> str:
    classification = classify(member, groups)
    assert classification.group is not None
    return classification.group.name


class TestDefaults:
    """Defaults when no configuration is present."""

    def test_default_settings_without_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        settings = load_settings()
        assert _group_names(settings) == DEFAULT_GROUP_NAMES
        assert settings.method_type_order == [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]
        assert settings.exclude == ()
        assert settings.sort_module is True
        assert settings.respect_dependencies is True

    def test_missing_tool_section(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[project]\nname = 'test'\n")
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES


class TestGroups:
    """The ``[[tool.funcsort.groups]]`` block fully replaces the defaults."""

    def test_groups_replace_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = (
            "[tool.funcsort]\n"
            "[[tool.funcsort.groups]]\n"
            'name = "tests"\n'
            'match = "^test_"\n'
            "[[tool.funcsort.groups]]\n"
            'name = "rest"\n'
            'match = ".*"\n'
        )
        _write(tmp_path, monkeypatch, content)
        settings = load_settings()
        assert _group_names(settings) == ["tests", "rest"]
        assert _group_for(_function("test_login"), settings.groups) == "tests"
        assert _group_for(_function("helper"), settings.groups) == "rest"

    def test_group_kind_and_scope_filters(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = (
            "[tool.funcsort]\n"
            "[[tool.funcsort.groups]]\n"
            'name = "constants"\n'
            'match = "^[A-Z]"\n'
            'kind = ["assignment"]\n'
            'scope = "module"\n'
        )
        _write(tmp_path, monkeypatch, content)
        group = load_settings().groups[0]
        assert group.kinds == frozenset({MemberKind.ASSIGNMENT})
        assert group.scopes == frozenset({Scope.MODULE})

    def test_group_decorator_filter(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = (
            "[tool.funcsort]\n"
            "[[tool.funcsort.groups]]\n"
            'name = "properties"\n'
            'match = ".*"\n'
            'decorator = ["property", "cached_property"]\n'
            "[[tool.funcsort.groups]]\n"
            'name = "rest"\n'
            'match = ".*"\n'
        )
        _write(tmp_path, monkeypatch, content)
        settings = load_settings()
        assert _group_for(_function("x", decorators=("property",)), settings.groups) == "properties"
        assert _group_for(_function("x"), settings.groups) == "rest"

    def test_invalid_regex_falls_back_to_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = '[tool.funcsort]\n[[tool.funcsort.groups]]\nname = "bad"\nmatch = "([unclosed"\n'
        _write(tmp_path, monkeypatch, content)
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES

    def test_group_missing_match_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = '[tool.funcsort]\n[[tool.funcsort.groups]]\nname = "incomplete"\n'
        _write(tmp_path, monkeypatch, content)
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES


class TestScalarSettings:
    """Method type order, excludes and the module toggle."""

    def test_method_type_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.funcsort]\nmethod_type_order = ["static", "instance", "class"]\n')
        assert load_settings().method_type_order == [MethodKind.STATIC, MethodKind.INSTANCE, MethodKind.CLASS]

    def test_invalid_method_type_order(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.funcsort]\nmethod_type_order = ["instance", "bogus"]\n')
        assert load_settings().method_type_order == [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]

    def test_exclude_patterns(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, '[tool.funcsort]\nexclude = ["tests/*", "migrations/*.py"]\n')
        assert load_settings().exclude == ("tests/*", "migrations/*.py")

    def test_sort_module_toggle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[tool.funcsort]\nsort_module = false\n")
        assert load_settings().sort_module is False

    def test_respect_dependencies_toggle(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[tool.funcsort]\nrespect_dependencies = false\n")
        assert load_settings().respect_dependencies is False


class TestDiscovery:
    """Config-file discovery and precedence."""

    def test_funcsort_toml_preferred_over_pyproject(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pyproject = '[tool.funcsort]\n[[tool.funcsort.groups]]\nname = "p"\nmatch = ".*"\n'
        funcsort = '[tool.funcsort]\n[[tool.funcsort.groups]]\nname = "f"\nmatch = ".*"\n'
        (tmp_path / "pyproject.toml").write_text(pyproject)
        (tmp_path / "funcsort.toml").write_text(funcsort)
        monkeypatch.chdir(tmp_path)
        assert _group_names(load_settings()) == ["f"]

    def test_found_in_parent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        content = '[tool.funcsort]\n[[tool.funcsort.groups]]\nname = "parent"\nmatch = ".*"\n'
        (tmp_path / "funcsort.toml").write_text(content)
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)
        assert _group_names(load_settings()) == ["parent"]

    def testfind_config_file_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        assert find_config_file() is None

    def testfind_config_file_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        path = tmp_path / "funcsort.toml"
        path.write_text("[tool.funcsort]\n")
        monkeypatch.chdir(tmp_path)
        assert find_config_file() == path

    def test_corrupted_toml_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        _write(tmp_path, monkeypatch, "[tool.funcsort\nmethod_type_order = [\n")
        assert _group_names(load_settings()) == DEFAULT_GROUP_NAMES
