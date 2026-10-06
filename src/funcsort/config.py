"""Configuration loading for funcsort.

Configuration is read with confkit from a dedicated ``funcsort.toml`` (preferred) or,
as a fallback, the ``[tool.funcsort]`` table of ``pyproject.toml``. Both files use the
same ``[tool.funcsort]`` section and keys. The values are resolved into a single
immutable :class:`Settings` value object that drives the engine.

Every config value is declared exactly once, as a :class:`Settings` field created with
:func:`option`. The field carries its confkit data type; :func:`load_settings` turns the
fields into confkit ``Config`` descriptors on a ``tool.funcsort`` container, so adding a
value never means touching a parallel schema, a docstring or the loader::

    sort_module: bool = option(True)

followed by its attribute docstring. Only a matching command-line flag, if the value
should have one, lives elsewhere (:mod:`funcsort.main`).
"""

from __future__ import annotations

from copy import copy
from dataclasses import Field, dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, overload

from confkit import BaseDataType, Config

from . import logger
from .config_types import GroupList, MethodTypeOrder, StringTuple
from .groups import FunctionPlacement

if TYPE_CHECKING:
    from .groups import Group, MethodKind

_CONFIG_FILENAMES = ("funcsort.toml", "pyproject.toml")
_SECTION = "tool.funcsort"
_DATA_TYPE = "confkit_data_type"
"""Field-metadata key holding the confkit data type an :func:`option` reads with."""


@overload
def option[T](default: BaseDataType[T]) -> T: ...
@overload
def option[T](default: T) -> T: ...
def option[T](default: T | BaseDataType[T]) -> T:
    """Declare one config value as a :class:`Settings` field.

    ``default`` is anything confkit's ``Config`` accepts: a plain value (``True``, a
    StrEnum member, ...) or a custom :class:`~confkit.BaseDataType`. The field's own
    default is what confkit reads back for an unset option, so a missing config file and
    an empty ``[tool.funcsort]`` table resolve identically.
    """
    data_type = BaseDataType.cast(default)
    return field(default_factory=lambda: _unset_value(data_type), metadata={_DATA_TYPE: data_type})


@dataclass(frozen=True)
class Settings:
    """Resolved funcsort configuration that drives the sorter.

    Each field is read from the ``[tool.funcsort]`` key of the same name.
    """

    groups: list[Group] = option(GroupList())
    """Ordered groups; output order and membership rules for members."""
    method_type_order: list[MethodKind] = option(MethodTypeOrder())
    """Secondary ordering of method types within each group."""
    exclude: tuple[str, ...] = option(StringTuple())
    """Glob patterns of files/directories to skip."""
    sort_module: bool = option(True)
    """Whether module-level functions are sorted or not."""
    respect_dependencies: bool = option(True)
    """
    Whether ordering keeps a definition ahead of anything that
    reads it at import time (decorators, parameter defaults, assignment values).
    """
    function_placement: FunctionPlacement = option(FunctionPlacement.AFTER_CLASSES)
    """Where module-level functions go relative to module-level classes."""


def load_settings() -> Settings:
    """Load and resolve configuration into a :class:`Settings`.

    Falls back to built-in defaults when no config file or section is present. An
    invalid value is reported and replaced by that option's default.
    """
    path = find_config_file()
    if path is None:
        return Settings()

    class _Cfg(Config[Any]): ...

    _Cfg.write_on_edit = False
    _Cfg.set_file(path)

    # confkit derives the section from the container's qualname, so naming it
    # ``tool.funcsort`` addresses ``[tool.funcsort]``. Each descriptor gets its own copy
    # of the data type: confkit stores the last read value on it.
    options = fields(Settings)
    container = type(
        "funcsort",
        (),
        {"__qualname__": _SECTION, **{entry.name: _Cfg(copy(_data_type(entry))) for entry in options}},
    )
    values: dict[str, Any] = {}
    for entry in options:
        try:
            values[entry.name] = getattr(container, entry.name)
        except ValueError as exc:
            logger.warning(f"Invalid {entry.name} in {path}: {exc}. Using the default.")
    return Settings(**values)


def find_config_file() -> Path | None:
    """Find ``funcsort.toml`` (preferred) or ``pyproject.toml`` in cwd or a parent."""
    current_dir = Path.cwd()
    for directory in [current_dir, *current_dir.parents]:
        for filename in _CONFIG_FILENAMES:
            candidate = directory / filename
            if candidate.exists():
                return candidate
    return None


def _data_type(entry: Field[Any]) -> BaseDataType[Any]:
    """Return the confkit data type an :func:`option` field was declared with."""
    return entry.metadata[_DATA_TYPE]


def _unset_value[T](data_type: BaseDataType[T]) -> T:
    """Return what confkit reads back for an option that is absent from the file."""
    return data_type.convert(str(data_type))
