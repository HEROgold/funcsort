"""Core sorting logic for class methods and module-level functions.

The transformer is a thin libcst adapter over the pure model in :mod:`undersort.groups`.
A single shared helper (:func:`_sort_block`) reorders the members of any block — a class
body or the module body — so class and module scope share one implementation.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import libcst as cst

from undersort import logger
from undersort.groups import (
    DEFAULT_CREATIONAL_DUNDERS,
    Group,
    Member,
    MemberKind,
    MethodKind,
    Scope,
    classify,
    default_groups,
    groups_for_order,
)

# Re-exported for backward compatibility with callers that imported it from here.
__all__ = ["DEFAULT_CREATIONAL_DUNDERS", "BlockSortResult", "MethodSorter", "SortResult", "sort_file"]


@dataclass(frozen=True)
class BlockSortResult:
    """Outcome of sorting a single block (class or module body)."""

    new_body: list[cst.BaseStatement]
    modified: bool
    unmatched: tuple[Member, ...]


@dataclass(frozen=True)
class SortResult:
    """Outcome of sorting a whole file."""

    path: Path
    modified: bool
    unmatched: tuple[Member, ...] = ()


def has_nosort_comment(node: cst.CSTNode) -> bool:
    """Return whether ``node`` carries a ``# nosort`` directive.

    Looks at leading comment lines, a trailing comment on the same line (assignments),
    and the trailing comment of a block header (functions/classes). Case-insensitive.
    """
    for line in getattr(node, "leading_lines", []):
        if isinstance(line, cst.EmptyLine) and line.comment and "nosort" in line.comment.value.lower():
            return True

    trailing = getattr(node, "trailing_whitespace", None)
    if isinstance(trailing, cst.TrailingWhitespace) and trailing.comment and "nosort" in trailing.comment.value.lower():
        return True

    body = getattr(node, "body", None)
    header = getattr(body, "header", None)
    return bool(
        isinstance(header, cst.TrailingWhitespace) and header.comment and "nosort" in header.comment.value.lower(),
    )


def file_has_nosort(module: cst.Module) -> bool:
    """Return whether the file has a ``# nosort: file`` directive in its header."""
    for line in module.header:
        if isinstance(line, cst.EmptyLine) and line.comment:
            comment_text = line.comment.value.lower()
            if "nosort" in comment_text and "file" in comment_text:
                return True
    return False


def get_method_type(method: cst.FunctionDef) -> MethodKind:
    """Determine a function's binding from its decorators."""
    for decorator in method.decorators:
        decorator_name = decorator.decorator
        if isinstance(decorator_name, cst.Name):
            if decorator_name.value == "classmethod":
                return MethodKind.CLASS
            if decorator_name.value == "staticmethod":
                return MethodKind.STATIC
    return MethodKind.INSTANCE


def get_method_visibility(
    method_name: str,
    creational_dunders: tuple[str, ...] | list[str] | None = None,
) -> str:
    """Return the default group name a method name falls into (compatibility shim)."""
    dunders = tuple(creational_dunders) if creational_dunders is not None else DEFAULT_CREATIONAL_DUNDERS
    member = Member(0, None, MemberKind.FUNCTION, method_name, MethodKind.INSTANCE, Scope.CLASS)
    result = classify(member, default_groups(dunders))
    return result.group.name if result.group else "public"


def _assignment_target_name(line: cst.SimpleStatementLine) -> str | None:
    """Return the single simple target name of an assignment line, else None."""
    if len(line.body) != 1:
        return None
    statement = line.body[0]
    if isinstance(statement, cst.Assign):
        if len(statement.targets) == 1 and isinstance(statement.targets[0].target, cst.Name):
            return statement.targets[0].target.value
        return None
    if isinstance(statement, cst.AnnAssign) and isinstance(statement.target, cst.Name):
        return statement.target.value
    return None


def _as_member(index: int, node: cst.BaseStatement, scope: Scope) -> Member | None:
    """Build a :class:`Member` for a sortable statement, or None for structural items."""
    if isinstance(node, cst.FunctionDef):
        return Member(index, node, MemberKind.FUNCTION, node.name.value, get_method_type(node), scope)
    if isinstance(node, cst.SimpleStatementLine):
        name = _assignment_target_name(node)
        if name is not None:
            return Member(index, node, MemberKind.ASSIGNMENT, name, MethodKind.INSTANCE, scope)
    return None


def _effective_method_type_order(method_type_order: list[MethodKind]) -> list[MethodKind]:
    """Return the order with any missing method types appended (never drop members)."""
    return [*method_type_order, *(t for t in MethodKind if t not in method_type_order)]


# A candidate paired with its position in the candidate sequence (the index space the
# minimise-movement logic operates on, independent of anchored/structural items).
_Placed = tuple[int, Member]


def _order_bucket(bucket: Iterable[_Placed], current_position: int) -> list[Member]:
    """Order one bucket, minimising movement relative to the candidate sequence.

    Members that started before the bucket's span (or before what is already placed) go
    first, members within the span keep their place, and members after the span go last —
    each subgroup stable by original position.
    """
    positions = [position for position, _ in bucket]
    min_pos, max_pos = min(positions), max(positions)

    moved_down: list[Member] = []
    in_place: list[Member] = []
    moved_up: list[Member] = []
    for position, member in sorted(bucket, key=lambda placed: placed[0]):
        if position < min_pos or (current_position > 0 and position < current_position):
            moved_down.append(member)
        elif position > max_pos:
            moved_up.append(member)
        else:
            in_place.append(member)
    return moved_down + in_place + moved_up


def _sort_block(
    body: Sequence[cst.BaseStatement],
    *,
    scope: Scope,
    groups: list[Group],
    method_type_order: list[MethodKind],
) -> BlockSortResult:
    """Reorder the sortable members of a block, anchoring everything else in place."""
    items = list(body)
    assignments_sortable = any(group.targets_assignments() for group in groups)

    buckets: dict[tuple[str, MethodKind], list[_Placed]] = {}
    unmatched: list[Member] = []
    candidate_indices: list[int] = []
    for index, item in enumerate(items):
        member = _as_member(index, item, scope)
        if member is None or has_nosort_comment(item):
            continue
        if member.kind is MemberKind.ASSIGNMENT and not assignments_sortable:
            continue
        position = len(candidate_indices)
        candidate_indices.append(index)
        result = classify(member, groups)
        if result.group is None:
            unmatched.append(member)
        else:
            buckets.setdefault((result.group.name, member.method_type), []).append((position, member))

    if not candidate_indices:
        return BlockSortResult(items, modified=False, unmatched=())

    effective_order = _effective_method_type_order(method_type_order)
    ordered: list[Member] = []
    current_position = 0
    for group in groups:
        for method_type in effective_order:
            bucket = buckets.get((group.name, method_type))
            if not bucket:
                continue
            ordered.extend(_order_bucket(bucket, current_position))
            current_position += len(bucket)
    ordered.extend(unmatched)

    ordered_nodes = [cast("cst.BaseStatement", member.node) for member in ordered]
    original_nodes = [items[index] for index in candidate_indices]
    modified = any(new is not old for new, old in zip(ordered_nodes, original_nodes, strict=True))

    fill = iter(ordered_nodes)
    candidate_set = set(candidate_indices)
    new_body = [next(fill) if index in candidate_set else item for index, item in enumerate(items)]

    return BlockSortResult(new_body, modified=modified, unmatched=tuple(unmatched))


class MethodSorter(cst.CSTTransformer):
    """Transformer that sorts class methods and (optionally) module-level functions."""

    def __init__(
        self,
        groups: Iterable[Group],
        method_type_order: Iterable[MethodKind],
        sort_module: bool = True,
    ) -> None:
        """Initialise the transformer with resolved configuration."""
        self.groups = groups
        self.method_type_order = method_type_order
        self.sort_module = sort_module
        self.modified = False
        self.unmatched: list[Member] = []

    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:  # noqa: N802, ARG002
        """Sort the methods within a class definition."""
        if has_nosort_comment(updated_node):
            return updated_node
        result = self._apply(list(updated_node.body.body), Scope.CLASS)
        if result is None:
            return updated_node
        return updated_node.with_changes(body=updated_node.body.with_changes(body=result))

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:  # noqa: N802, ARG002
        """Sort the top-level functions of a module when enabled."""
        if not self.sort_module:
            return updated_node
        result = self._apply(list(updated_node.body), Scope.MODULE)
        if result is None:
            return updated_node
        return updated_node.with_changes(body=result)

    def _apply(self, body: list[cst.BaseStatement], scope: Scope) -> list[cst.BaseStatement] | None:
        """Run the shared sorter on a block; record state; return the new body or None."""
        result = _sort_block(
            body,
            scope=scope,
            groups=self.groups,
            method_type_order=self.method_type_order,
        )
        self.unmatched.extend(result.unmatched)
        if not result.modified:
            return None
        self.modified = True
        return result.new_body


def _resolve_groups(
    groups: Iterable[Group] | None,
    order: Iterable[str] | None,
    creational_dunders: Iterable[str] | None,
) -> list[Group]:
    """Resolve explicit groups or a legacy ``order`` into a group list."""
    if groups is not None:
        return groups
    dunders = tuple(creational_dunders) if creational_dunders is not None else DEFAULT_CREATIONAL_DUNDERS
    if order is not None:
        return groups_for_order(order, dunders)
    return default_groups(dunders)


def _resolve_method_type_order(method_type_order: list[Any] | None) -> list[MethodKind]:
    """Coerce a method type order (strings or enums) into :class:`MethodKind` values."""
    if not method_type_order:
        return [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]
    return [MethodKind(value) for value in method_type_order]


def sort_file(
    file_path: Path,
    order: Iterable[str] | None = None,
    method_type_order: Iterable[Any] | None = None,
    check_only: bool = False,
    show_diff: bool = False,
    creational_dunders: Iterable[str] | None = None,
    *,
    groups: Iterable[Group] | None = None,
    sort_module: bool = True,
) -> SortResult:
    """Sort the methods (and optionally module functions) of a Python file.

    Either pass explicit ``groups`` (new API) or a legacy ``order`` list; when neither is
    given the built-in default groups are used.

    Returns:
        A :class:`SortResult` describing whether the file changed and any unmatched members.
    """
    with open(file_path, encoding="utf-8") as f:
        source_code = f.read()

    try:
        tree = cst.parse_module(source_code)
    except cst.ParserSyntaxError as e:
        raise ValueError(f"Syntax error in {file_path}: {e}")

    if file_has_nosort(tree):
        return SortResult(file_path, modified=False)

    sorter = MethodSorter(
        groups=_resolve_groups(groups, order, creational_dunders),
        method_type_order=_resolve_method_type_order(method_type_order),
        sort_module=sort_module,
    )
    new_tree = tree.visit(sorter)

    if not sorter.modified:
        return SortResult(file_path, modified=False, unmatched=tuple(sorter.unmatched))

    new_code = new_tree.code

    if show_diff:
        diff = difflib.unified_diff(
            source_code.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            fromfile=str(file_path),
            tofile=str(file_path),
        )
        logger.diff("".join(diff))

    if not check_only:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_code)

    return SortResult(file_path, modified=True, unmatched=tuple(sorter.unmatched))
