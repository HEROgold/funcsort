"""Core sorting logic for class methods and module-level functions.

The transformer is a thin libcst adapter over the pure model in :mod:`funcsort.groups`.
A single shared helper (:func:`sort_block`) reorders the members of any block — a class
body or the module body — so class and module scope share one implementation.

Ordering is not purely a grouping question. A statement that reads a name while it
executes — a decorator expression, a parameter default — must stay after whatever binds
that name, or the sorted file raises ``NameError`` on import. :mod:`funcsort.references`
extracts those load-time dependencies and :mod:`funcsort.ordering` fits the preferred
order to them.
"""

from __future__ import annotations

import difflib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast, override

import libcst as cst

from funcsort.groups import (
    Group,
    Member,
    MemberKind,
    MethodKind,
    Scope,
    classify,
    default_groups,
)
from funcsort.ordering import (
    OrderingOutcome,
    OrderingProblem,
    OrderingResult,
    Statement,
    solve_order,
)
from funcsort.references import EMPTY_FLOW, name_flow, uses_future_annotations

from . import logger

_DEFAULT_METHOD_TYPE_ORDER = [MethodKind.INSTANCE, MethodKind.CLASS, MethodKind.STATIC]

# Bucketing is a minimise-movement heuristic, so its result depends on where members
# started. Sorting is therefore iterated to a fixed point: the order finally emitted is one
# that re-sorts to itself, which is exactly what makes a second funcsort run a no-op.
# Convergence takes two passes in practice; the bound only stops a pathological block
# from spinning.
_MAX_ORDERING_PASSES = 8


@dataclass(frozen=True)
class BlockSortResult:
    """Outcome of sorting a single block (class or module body)."""

    new_body: list[cst.BaseStatement]
    modified: bool
    unmatched: tuple[Member, ...]
    outcome: OrderingOutcome = OrderingOutcome.UNCONSTRAINED


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
        if isinstance(line, cst.EmptyLine) and line.comment:  # pyright: ignore[reportUnnecessaryIsInstance]
            comment_text = line.comment.value.lower()
            if "nosort" in comment_text and "file" in comment_text:
                return True
    return False


def get_method_type(method: cst.FunctionDef) -> MethodKind:
    """Determine a function's binding from its decorators."""
    for name in _decorator_names(method):
        if name == "classmethod":
            return MethodKind.CLASS
        if name == "staticmethod":
            return MethodKind.STATIC
    return MethodKind.INSTANCE


def sort_block(
    body: Sequence[cst.BaseStatement],
    *,
    scope: Scope,
    groups: list[Group],
    method_type_order: list[MethodKind],
    respect_dependencies: bool = True,
    lazy_annotations: bool = False,
) -> BlockSortResult:
    """Reorder the sortable members of a block, anchoring everything else in place.

    Disabling ``respect_dependencies`` restores pure group ordering, which can emit a file
    that no longer imports. ``lazy_annotations`` records that the module defers annotations
    (PEP 563), in which case annotation references impose no ordering at all.
    """
    items = list(body)
    plan = _plan_block(
        items,
        scope=scope,
        groups=groups,
        method_type_order=method_type_order,
        respect_dependencies=respect_dependencies,
        lazy_annotations=lazy_annotations,
    )
    if plan is None:
        return BlockSortResult(items, modified=False, unmatched=())

    order, outcome = _converge(plan)
    return _emit(items, plan, order, outcome)


@dataclass(frozen=True)
class _BlockPlan:
    """Everything about a block that stays constant however its candidates are permuted.

    Splitting this out is what makes iterating to a fixed point cheap: the libcst work
    (classification and name-flow extraction) happens once, and only the pure bucketing
    and constraint solving repeat.
    """

    members: Mapping[int, Member]
    """Candidate body index to its classified member."""

    bucket_keys: Mapping[int, tuple[str, MethodKind]]
    """Candidate body index to its bucket; candidates that matched no group are absent."""

    unmatched: tuple[Member, ...]
    problem: OrderingProblem
    groups: list[Group]
    """Ordered groups, giving the bucket emission order."""

    method_type_order: list[MethodKind]
    """Secondary ordering, already expanded to cover every kind."""

    @property
    def identity(self) -> tuple[int, ...]:
        """Return the candidates in their original order (always a safe ordering)."""
        return self.problem.slots

    def step(self, order: Sequence[int]) -> OrderingResult:
        """Bucket-sort ``order``, then fit the result to the dependency constraints."""
        return solve_order(self.problem.with_desired(self._desired(order)))

    def _desired(self, order: Sequence[int]) -> tuple[int, ...]:
        """Return the order the groups want, given where the candidates currently sit."""
        buckets: dict[tuple[str, MethodKind], list[_Placed]] = {}
        unmatched: list[int] = []
        for position, index in enumerate(order):
            key = self.bucket_keys.get(index)
            if key is None:
                unmatched.append(index)
            else:
                buckets.setdefault(key, []).append((position, index))

        desired: list[int] = []
        current_position = 0
        for group in self.groups:
            for method_type in self.method_type_order:
                bucket = buckets.get((group.name, method_type))
                if not bucket:
                    continue
                desired.extend(_order_bucket(bucket, current_position))
                current_position += len(bucket)
        desired.extend(unmatched)
        return tuple(desired)


def sort_file(
    file_path: Path,
    groups: list[Group] | None = None,
    method_type_order: list[MethodKind] | None = None,
    *,
    sort_module: bool = True,
    check_only: bool = False,
    show_diff: bool = False,
    respect_dependencies: bool = True,
) -> SortResult:
    """Sort the methods (and optionally module functions) of a Python file.

    Omitting ``groups`` or ``method_type_order`` falls back to the built-in defaults.
    Under ``check_only`` nothing is written back to disk. Disabling
    ``respect_dependencies`` can produce a file that no longer imports.
    """
    resolved_groups = groups if groups is not None else default_groups()
    resolved_order = method_type_order if method_type_order is not None else list(_DEFAULT_METHOD_TYPE_ORDER)

    with open(file_path, encoding="utf-8") as f:
        source_code = f.read()

    try:
        tree = cst.parse_module(source_code)
    except cst.ParserSyntaxError as e:
        raise ValueError(f"Syntax error in {file_path}: {e}")

    if file_has_nosort(tree):
        return SortResult(file_path, modified=False)

    sorter = MethodSorter(
        resolved_groups,
        resolved_order,
        sort_module=sort_module,
        respect_dependencies=respect_dependencies,
    )
    new_tree = tree.visit(sorter)

    if sorter.blocked:
        logger.warning(
            f"Could not satisfy load-time dependencies in {file_path}; the affected block was left unsorted.",
        )

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


def _plan_block(
    items: list[cst.BaseStatement],
    *,
    scope: Scope,
    groups: list[Group],
    method_type_order: list[MethodKind],
    respect_dependencies: bool,
    lazy_annotations: bool,
) -> _BlockPlan | None:
    """Classify a block into candidates and anchors, or return None if nothing can move."""
    assignments_sortable = any(group.targets_assignments() for group in groups)

    members: dict[int, Member] = {}
    bucket_keys: dict[int, tuple[str, MethodKind]] = {}
    unmatched: list[Member] = []
    candidates: list[Statement] = []
    anchors: list[Statement] = []
    for index, item in enumerate(items):
        member = _as_member(index, item, scope)
        flow = name_flow(item, lazy_annotations=lazy_annotations) if respect_dependencies else EMPTY_FLOW
        statement = Statement(index, flow.provides, flow.requires)

        if member is None or has_nosort_comment(item):
            anchors.append(statement)
            continue
        if member.kind is MemberKind.ASSIGNMENT and not assignments_sortable:
            anchors.append(statement)
            continue

        candidates.append(statement)
        members[index] = member
        result = classify(member, groups)
        if result.group is None:
            unmatched.append(member)
        else:
            bucket_keys[index] = (result.group.name, member.method_type)

    if not candidates:
        return None

    slots = tuple(statement.index for statement in candidates)
    return _BlockPlan(
        members=members,
        bucket_keys=bucket_keys,
        unmatched=tuple(unmatched),
        problem=OrderingProblem(tuple(anchors), tuple(candidates), slots, slots),
        groups=groups,
        method_type_order=_effective_method_type_order(method_type_order),
    )


def _converge(plan: _BlockPlan) -> tuple[tuple[int, ...], OrderingOutcome]:
    """Iterate bucketing and constraint solving until the order sorts to itself.

    A returned fixed point makes a second funcsort run a no-op: re-sorting starts from
    that same order and immediately reproduces it. If the block cannot be ordered safely,
    or oscillates instead of settling, the original order is returned — always safe,
    because the file being sorted already runs.
    """
    current = plan.identity
    seen = {current}
    repaired = False
    for _ in range(_MAX_ORDERING_PASSES):
        result = plan.step(current)
        if not result.is_safe:
            return plan.identity, OrderingOutcome.INFEASIBLE
        repaired = repaired or result.outcome is OrderingOutcome.REPAIRED
        if result.order == current:
            return current, OrderingOutcome.REPAIRED if repaired else OrderingOutcome.UNCONSTRAINED
        if result.order in seen:
            return plan.identity, OrderingOutcome.INFEASIBLE
        seen.add(result.order)
        current = result.order
    return plan.identity, OrderingOutcome.INFEASIBLE


def _emit(
    items: list[cst.BaseStatement],
    plan: _BlockPlan,
    order: Sequence[int],
    outcome: OrderingOutcome,
) -> BlockSortResult:
    """Pour the ordered candidates back into their slots, leaving anchors untouched."""
    if tuple(order) == plan.identity:
        return BlockSortResult(items, modified=False, unmatched=plan.unmatched, outcome=outcome)

    fill = iter(cast("cst.BaseStatement", plan.members[index].node) for index in order)
    slots = set(plan.identity)
    new_body = [next(fill) if index in slots else item for index, item in enumerate(items)]
    return BlockSortResult(new_body, modified=True, unmatched=plan.unmatched, outcome=outcome)


def _decorator_names(method: cst.FunctionDef) -> tuple[str, ...]:
    """Return the normalised dotted names of a function's decorators (calls stripped)."""
    return tuple(name for name in (_decorator_name(d.decorator) for d in method.decorators) if name)


def _decorator_name(node: cst.BaseExpression) -> str:
    """Normalise a decorator expression to its dotted name (``a.b.c``), stripping calls."""
    if isinstance(node, cst.Call):
        node = node.func
    parts: list[str] = []
    while isinstance(node, cst.Attribute):
        parts.append(node.attr.value)
        node = node.value
    if isinstance(node, cst.Name):
        parts.append(node.value)
    return ".".join(reversed(parts))


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


# A candidate's body index paired with its position in the candidate sequence (the index
# space the minimise-movement logic operates on, independent of anchored/structural items).
_Placed = tuple[int, int]


def _as_member(index: int, node: cst.BaseStatement, scope: Scope) -> Member | None:
    """Build a :class:`Member` for a sortable statement, or None for structural items."""
    if isinstance(node, cst.FunctionDef):
        return Member(
            index,
            node,
            MemberKind.FUNCTION,
            node.name.value,
            get_method_type(node),
            scope,
            _decorator_names(node),
        )
    if isinstance(node, cst.SimpleStatementLine):
        name = _assignment_target_name(node)
        if name is not None:
            return Member(index, node, MemberKind.ASSIGNMENT, name, MethodKind.INSTANCE, scope)
    return None


def _effective_method_type_order(method_type_order: list[MethodKind]) -> list[MethodKind]:
    """Return the order with any missing method types appended (never drop members)."""
    return [*method_type_order, *(t for t in MethodKind if t not in method_type_order)]


class MethodSorter(cst.CSTTransformer):
    """Transformer that sorts class methods and (optionally) module-level functions."""

    def __init__(
        self,
        groups: list[Group],
        method_type_order: list[MethodKind],
        sort_module: bool = True,
        respect_dependencies: bool = True,
    ) -> None:
        """Initialise the transformer with resolved configuration."""
        self.groups = groups
        self.method_type_order = method_type_order
        self.sort_module = sort_module
        self.respect_dependencies = respect_dependencies
        self.lazy_annotations = False
        self.modified = False
        self.blocked = False
        self.unmatched: list[Member] = []

    @override
    def visit_Module(self, node: cst.Module) -> bool:
        """Record whether the module defers annotations, before any body is visited.

        This has to be a ``visit_`` hook: ``leave_ClassDef`` fires before ``leave_Module``,
        so reading the future import on the way out would be too late for class bodies.
        """
        self.lazy_annotations = self.respect_dependencies and uses_future_annotations(node)
        return True

    @override
    def leave_ClassDef(self, original_node: cst.ClassDef, updated_node: cst.ClassDef) -> cst.ClassDef:
        """Sort the methods within a class definition."""
        body = updated_node.body
        if has_nosort_comment(updated_node) or not isinstance(body, cst.IndentedBlock):
            return updated_node
        new_body = self._apply(list(body.body), Scope.CLASS)
        if new_body is None:
            return updated_node
        return updated_node.with_changes(body=body.with_changes(body=new_body))

    @override
    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        """Sort the top-level functions of a module when enabled."""
        if not self.sort_module:
            return updated_node
        new_body = self._apply(list(updated_node.body), Scope.MODULE)
        if new_body is None:
            return updated_node
        return updated_node.with_changes(body=new_body)

    def _apply(self, body: list[cst.BaseStatement], scope: Scope) -> list[cst.BaseStatement] | None:
        """Run the shared sorter on a block; record state; return the new body or None."""
        result = sort_block(
            body,
            scope=scope,
            groups=self.groups,
            method_type_order=self.method_type_order,
            respect_dependencies=self.respect_dependencies,
            lazy_annotations=self.lazy_annotations,
        )
        self.unmatched.extend(result.unmatched)
        self.blocked = self.blocked or result.outcome is OrderingOutcome.INFEASIBLE
        if not result.modified:
            return None
        self.modified = True
        return result.new_body


def _order_bucket(bucket: list[_Placed], current_position: int) -> list[int]:
    """Order one bucket, minimising movement relative to the candidate sequence.

    Members that started before the bucket's span (or before what is already placed) go
    first, members within the span keep their place, and members after the span go last —
    each subgroup stable by original position.
    """
    positions = [position for position, _ in bucket]
    min_pos, max_pos = min(positions), max(positions)

    moved_down: list[int] = []
    in_place: list[int] = []
    moved_up: list[int] = []
    for position, index in sorted(bucket, key=lambda placed: placed[0]):
        if position < min_pos or (current_position > 0 and position < current_position):
            moved_down.append(index)
        elif position > max_pos:
            moved_up.append(index)
        else:
            in_place.append(index)
    return moved_down + in_place + moved_up
