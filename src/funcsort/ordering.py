"""Load-time dependency constraints over a block's statement order.

This is a pure module: it knows nothing about libcst, TOML or groups. It takes the order
the grouping engine *wants* and returns an order that is also *safe* to execute.

The model exploits the shape the sorter already has. Statements that cannot be sorted --
imports, ``if``/``try`` blocks, nested classes -- are **anchors** pinned to their original
index, and the sortable **candidates** are poured into the remaining **slots**. So the
question is never "what is the best permutation of the body"; it is only "which candidate
goes in which slot", subject to:

* **precedence** -- a candidate that defines a name another candidate reads at load time
  must take an earlier slot;
* **release times** -- a candidate reading a name an anchor defines must land after it;
* **deadlines** -- a candidate defining a name an anchor reads must land before it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

_NO_RELEASE = -1
_NO_DEADLINE = float("inf")


class OrderingOutcome(StrEnum):
    """How the desired order fared against the block's dependency constraints."""

    UNCONSTRAINED = "unconstrained"
    """The desired order was already safe and is emitted verbatim."""

    REPAIRED = "repaired"
    """The desired order was unsafe; a different, safe order was found."""

    INFEASIBLE = "infeasible"
    """No safe order was found; the caller must keep the block as it was."""


@dataclass(frozen=True)
class Statement:
    """A statement's position within its block body, and its load-time name flow."""

    index: int
    provides: frozenset[str] = frozenset()
    requires: frozenset[str] = frozenset()
    """Names read *while the statement executes* -- a decorator expression or a parameter
    default, never a function body, which runs long after the module is loaded."""


@dataclass(frozen=True)
class OrderingProblem:
    """A block's candidates, its fixed anchors, and the order the groups want."""

    anchors: tuple[Statement, ...]
    """Statements pinned to their own index; they never move."""

    candidates: tuple[Statement, ...]
    slots: tuple[int, ...]
    """Ascending body indices the candidates may occupy -- the anchors' complement."""

    desired: tuple[int, ...]

    def with_desired(self, desired: Sequence[int]) -> OrderingProblem:
        """Return a copy of this problem with a different preferred order."""
        return OrderingProblem(self.anchors, self.candidates, self.slots, tuple(desired))


@dataclass(frozen=True)
class OrderingResult:
    """The order to emit, and how it was arrived at."""

    order: tuple[int, ...]
    """Candidate indices in the order they should fill :attr:`OrderingProblem.slots`."""

    outcome: OrderingOutcome

    @property
    def is_safe(self) -> bool:
        """Return whether :attr:`order` satisfies the block's dependencies."""
        return self.outcome is not OrderingOutcome.INFEASIBLE


@dataclass(frozen=True)
class _Constraints:
    """Derived scheduling constraints, all invariant under candidate permutation."""

    predecessors: Mapping[int, frozenset[int]]
    release: Mapping[int, int]
    deadline: Mapping[int, float]
    pinned: frozenset[int]
    slot_of: Mapping[int, int] = field(default_factory=dict)

    @property
    def is_trivial(self) -> bool:
        """Return whether nothing constrains the order (the common case)."""
        return (
            not self.pinned
            and not any(self.predecessors.values())
            and all(value == _NO_RELEASE for value in self.release.values())
            and all(value == _NO_DEADLINE for value in self.deadline.values())
        )


def solve_order(problem: OrderingProblem) -> OrderingResult:
    """Fit the desired order to the block's load-time dependencies.

    On :attr:`OrderingOutcome.INFEASIBLE` the returned order is the block's original one,
    which is always safe because the input file already runs.
    """
    identity = tuple(statement.index for statement in sorted(problem.candidates, key=lambda s: s.index))
    constraints = _derive(problem)

    if constraints.is_trivial:
        return OrderingResult(tuple(problem.desired), OrderingOutcome.UNCONSTRAINED)

    # The input file is valid Python, so its own order must satisfy anything we derived.
    # If it does not, the extraction is over-conservative past the point of solvability --
    # bail out rather than emit a guess.
    if not _satisfies(identity, problem.slots, constraints):
        return OrderingResult(identity, OrderingOutcome.INFEASIBLE)

    if _satisfies(problem.desired, problem.slots, constraints):
        return OrderingResult(tuple(problem.desired), OrderingOutcome.UNCONSTRAINED)

    repaired = _repair(problem, constraints)
    if repaired is None:
        return OrderingResult(identity, OrderingOutcome.INFEASIBLE)
    return OrderingResult(repaired, OrderingOutcome.REPAIRED)


def _derive(problem: OrderingProblem) -> _Constraints:
    """Build the precedence edges, release times, deadlines and pins for a block."""
    slot_of = {index: position for position, index in enumerate(problem.slots)}
    providers: dict[str, list[int]] = {}
    for candidate in problem.candidates:
        for name in candidate.provides:
            providers.setdefault(name, []).append(candidate.index)

    anchor_provides = _union(anchor.provides for anchor in problem.anchors)

    # A name bound more than once makes "the provider comes first" ambiguous: which
    # binding a reader sees depends on order, so no single edge expresses it. Pin every
    # candidate involved to its own slot instead. Rare, and conservative.
    pinned = {index for name, indices in providers.items() if len(indices) > 1 or name in anchor_provides for index in indices}

    predecessors: dict[int, frozenset[int]] = {}
    release: dict[int, int] = {}
    deadline: dict[int, float] = {}
    for candidate in problem.candidates:
        needed = {
            providers[name][0]
            for name in candidate.requires
            if name in providers and len(providers[name]) == 1 and providers[name][0] != candidate.index
        }
        predecessors[candidate.index] = frozenset(needed)
        release[candidate.index] = max(
            (anchor.index for anchor in problem.anchors if anchor.provides & candidate.requires),
            default=_NO_RELEASE,
        )
        deadline[candidate.index] = min(
            (float(anchor.index) for anchor in problem.anchors if anchor.requires & candidate.provides),
            default=_NO_DEADLINE,
        )

    return _Constraints(predecessors, release, deadline, frozenset(pinned), slot_of)


def _satisfies(order: Sequence[int], slots: Sequence[int], constraints: _Constraints) -> bool:
    """Return whether ``order`` places every candidate in a legal slot."""
    position_of = {index: position for position, index in enumerate(order)}
    for position, index in enumerate(order):
        body_index = slots[position]
        if constraints.release[index] > body_index or constraints.deadline[index] < body_index:
            return False
        if index in constraints.pinned and constraints.slot_of[index] != position:
            return False
        if any(position_of[predecessor] > position for predecessor in constraints.predecessors[index]):
            return False
    return True


def _repair(problem: OrderingProblem, constraints: _Constraints) -> tuple[int, ...] | None:
    """Schedule candidates into slots greedily, or return None if the greedy stalls.

    Slots are filled in ascending order. Among the candidates that are legal *now*, the
    one with the earliest deadline wins, falling back to the desired order. When no
    deadlines exist -- the ordinary case -- every key is ``(inf, rank)``, so this
    degenerates to pure desired order and output quality is untouched.
    """
    rank = {index: position for position, index in enumerate(problem.desired)}
    unmet = {index: len(predecessors) for index, predecessors in constraints.predecessors.items()}
    successors: dict[int, list[int]] = {index: [] for index in unmet}
    for index, predecessors in constraints.predecessors.items():
        for predecessor in predecessors:
            successors[predecessor].append(index)

    remaining = set(problem.desired)
    order: list[int] = []
    for position, body_index in enumerate(problem.slots):
        eligible = [
            index
            for index in remaining
            if unmet[index] == 0
            and constraints.release[index] < body_index
            and constraints.deadline[index] > body_index
            and (index not in constraints.pinned or constraints.slot_of[index] == position)
        ]
        if not eligible:
            return None
        pick = min(eligible, key=lambda index: (constraints.deadline[index], rank[index]))
        order.append(pick)
        remaining.discard(pick)
        for successor in successors[pick]:
            unmet[successor] -= 1

    return tuple(order)


def _union(sets: Iterable[frozenset[str]]) -> frozenset[str]:
    """Return the union of an iterable of frozensets."""
    return frozenset().union(*sets)
