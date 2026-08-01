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

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

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

    # A failed repair is not an error: the block simply keeps the order it came in with.
    repaired = _repair(problem, constraints)
    return (
        OrderingResult(identity, OrderingOutcome.INFEASIBLE)
        if repaired is None
        else OrderingResult(repaired, OrderingOutcome.REPAIRED)
    )


def _derive(problem: OrderingProblem) -> _Constraints:
    """Build the precedence edges, release times, deadlines and pins for a block."""
    providers = _providers(problem.candidates)
    anchors = problem.anchors
    return _Constraints(
        predecessors={candidate.index: _predecessors(candidate, providers) for candidate in problem.candidates},
        release={candidate.index: _release(candidate, anchors) for candidate in problem.candidates},
        deadline={candidate.index: _deadline(candidate, anchors) for candidate in problem.candidates},
        pinned=_pinned(providers, _union(anchor.provides for anchor in anchors)),
        slot_of={index: position for position, index in enumerate(problem.slots)},
    )


def _providers(candidates: Sequence[Statement]) -> Mapping[str, Sequence[int]]:
    """Map every name a candidate binds to the candidates that bind it, in body order."""
    providers: dict[str, list[int]] = {}
    for candidate in candidates:
        for name in candidate.provides:
            providers.setdefault(name, []).append(candidate.index)
    return providers


def _pinned(providers: Mapping[str, Sequence[int]], anchor_provides: frozenset[str]) -> frozenset[int]:
    """Return the candidates whose bindings are too ambiguous to express as an edge.

    A name bound more than once makes "the provider comes first" ambiguous: which binding
    a reader sees depends on order, so no single edge expresses it. Every candidate
    involved is pinned to its own slot instead. Rare, and conservative.
    """
    return frozenset(
        index for name, indices in providers.items() if len(indices) > 1 or name in anchor_provides for index in indices
    )


def _predecessors(candidate: Statement, providers: Mapping[str, Sequence[int]]) -> frozenset[int]:
    """Return the candidates that must take an earlier slot than ``candidate``.

    Only unambiguously bound names produce an edge; the rest are handled by :func:`_pinned`.
    """
    return frozenset(
        providers[name][0]
        for name in candidate.requires
        if name in providers and len(providers[name]) == 1 and providers[name][0] != candidate.index
    )


def _release(candidate: Statement, anchors: Sequence[Statement]) -> int:
    """Return the body index below which ``candidate`` may not sink."""
    return max(
        (anchor.index for anchor in anchors if anchor.provides & candidate.requires),
        default=_NO_RELEASE,
    )


def _deadline(candidate: Statement, anchors: Sequence[Statement]) -> float:
    """Return the body index above which ``candidate`` may not rise."""
    return min(
        (float(anchor.index) for anchor in anchors if anchor.requires & candidate.provides),
        default=_NO_DEADLINE,
    )


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
    successors = _successors(constraints.predecessors)

    remaining = set(problem.desired)
    order: list[int] = []
    for position, body_index in enumerate(problem.slots):
        eligible = [
            index for index in remaining if _is_eligible(index, constraints, unmet, position=position, body_index=body_index)
        ]
        if not eligible:
            return None
        pick = min(eligible, key=lambda index: (constraints.deadline[index], rank[index]))
        order.append(pick)
        remaining.discard(pick)
        # Kahn bookkeeping: the inner loop walks one candidate's out-edges, so the whole
        # slot loop costs O(edges) in total, not O(candidates**2).
        for successor in successors[pick]:  # skylos: ignore[SKY-P403] O(edges) in total
            unmet[successor] -= 1

    return tuple(order)


def _successors(predecessors: Mapping[int, frozenset[int]]) -> Mapping[int, Sequence[int]]:
    """Invert the precedence edges, so releasing a candidate is a single lookup."""
    successors: dict[int, list[int]] = {index: [] for index in predecessors}
    for index, required in predecessors.items():
        for predecessor in required:  # skylos: ignore[SKY-P403] visits each edge once: O(edges)
            successors[predecessor].append(index)
    return successors


def _is_eligible(index: int, constraints: _Constraints, unmet: Mapping[int, int], *, position: int, body_index: int) -> bool:
    """Return whether a candidate may legally take the slot at ``position``."""
    return (
        unmet[index] == 0
        and constraints.release[index] < body_index
        and constraints.deadline[index] > body_index
        and (index not in constraints.pinned or constraints.slot_of[index] == position)
    )


def _union(sets: Iterable[frozenset[str]]) -> frozenset[str]:
    """Return the union of an iterable of frozensets."""
    return frozenset().union(*sets)
