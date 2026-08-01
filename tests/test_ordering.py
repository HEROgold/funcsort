"""Tests for the pure constraint solver — no libcst, no config, no parsing."""

from funcsort.ordering import (
    OrderingOutcome,
    OrderingProblem,
    OrderingResult,
    Statement,
    solve_order,
)


def _statement(index: int, provides: str = "", requires: str = "") -> Statement:
    """Build a statement from space-separated name lists."""
    return Statement(index, frozenset(provides.split()), frozenset(requires.split()))


def _problem(
    candidates: list[Statement],
    desired: list[int],
    anchors: list[Statement] | None = None,
    slots: list[int] | None = None,
) -> OrderingProblem:
    """Build a problem, defaulting the slots to the candidates' own indices."""
    return OrderingProblem(
        anchors=tuple(anchors or []),
        candidates=tuple(candidates),
        slots=tuple(slots if slots is not None else [c.index for c in candidates]),
        desired=tuple(desired),
    )


class TestFastPath:
    """Blocks with no dependencies must pass straight through."""

    def test_no_constraints_returns_desired_verbatim(self) -> None:
        result = solve_order(_problem([_statement(0, "a"), _statement(1, "b")], desired=[1, 0]))
        assert result == OrderingResult((1, 0), OrderingOutcome.UNCONSTRAINED)

    def test_lazy_reference_creates_no_edge(self) -> None:
        # Nothing requires anything: mutual recursion in function bodies looks like this.
        result = solve_order(_problem([_statement(0, "_a"), _statement(1, "b")], desired=[1, 0]))
        assert result.outcome is OrderingOutcome.UNCONSTRAINED

    def test_already_valid_desired_order_is_kept(self) -> None:
        candidates = [_statement(0, "_helper"), _statement(1, "pub", "_helper")]
        result = solve_order(_problem(candidates, desired=[0, 1]))
        assert result == OrderingResult((0, 1), OrderingOutcome.UNCONSTRAINED)


class TestPrecedence:
    """Candidate-to-candidate edges."""

    def test_violated_edge_is_repaired(self) -> None:
        # `pub` reads `_helper` at load time, but the groups want `pub` first.
        candidates = [_statement(0, "_helper"), _statement(1, "pub", "_helper")]
        result = solve_order(_problem(candidates, desired=[1, 0]))
        assert result == OrderingResult((0, 1), OrderingOutcome.REPAIRED)

    def test_unrelated_candidates_keep_their_preferred_order(self) -> None:
        candidates = [_statement(0, "_helper"), _statement(1, "pub", "_helper"), _statement(2, "other")]
        result = solve_order(_problem(candidates, desired=[1, 2, 0]))
        # `_helper` must precede `pub`, but `other` is free and keeps its relative place.
        assert result.outcome is OrderingOutcome.REPAIRED
        assert result.order.index(0) < result.order.index(1)
        assert set(result.order) == {0, 1, 2}

    def test_chain_of_edges(self) -> None:
        candidates = [_statement(0, "a"), _statement(1, "b", "a"), _statement(2, "c", "b")]
        result = solve_order(_problem(candidates, desired=[2, 1, 0]))
        assert result.order == (0, 1, 2)

    def test_self_reference_is_not_an_edge(self) -> None:
        result = solve_order(_problem([_statement(0, "x", "x"), _statement(1, "y")], desired=[1, 0]))
        assert result.outcome is OrderingOutcome.UNCONSTRAINED


class TestAnchors:
    """Release times and deadlines imposed by immovable statements."""

    def test_release_time_keeps_candidate_after_its_anchor(self) -> None:
        # Anchor at index 1 binds `CONST`; the candidate reading it cannot take slot 0.
        anchors = [_statement(1, "CONST")]
        candidates = [_statement(0, "free"), _statement(2, "user", "CONST")]
        result = solve_order(_problem(candidates, desired=[2, 0], anchors=anchors, slots=[0, 2]))
        assert result == OrderingResult((0, 2), OrderingOutcome.REPAIRED)

    def test_deadline_keeps_candidate_before_its_anchor(self) -> None:
        # Anchor at index 1 reads `_make`, so `_make` must stay in slot 0.
        anchors = [_statement(1, "REGISTRY", "_make")]
        candidates = [_statement(0, "_make"), _statement(2, "helper")]
        result = solve_order(_problem(candidates, desired=[2, 0], anchors=anchors, slots=[0, 2]))
        assert result == OrderingResult((0, 2), OrderingOutcome.REPAIRED)

    def test_deadline_and_release_together(self) -> None:
        anchors = [_statement(2, "MID", "_early")]
        candidates = [_statement(0, "_early"), _statement(1, "other"), _statement(3, "late", "MID")]
        result = solve_order(_problem(candidates, desired=[3, 1, 0], anchors=anchors, slots=[0, 1, 3]))
        assert result.order == (0, 1, 3)


class TestInfeasible:
    """When no safe order exists, the original order is returned untouched."""

    def test_greedy_stall_returns_identity(self) -> None:
        # `a` must precede `b` yet an anchor at index 0 requires `a` -- deadline 0 is
        # unreachable because slot 0 is the earliest slot and index 0 is the anchor.
        anchors = [_statement(0, "", "a")]
        candidates = [_statement(1, "a"), _statement(2, "b", "a")]
        result = solve_order(_problem(candidates, desired=[2, 1], anchors=anchors, slots=[1, 2]))
        assert result == OrderingResult((1, 2), OrderingOutcome.INFEASIBLE)
        assert result.is_safe is False

    def test_cyclic_edges_return_identity(self) -> None:
        candidates = [_statement(0, "a", "b"), _statement(1, "b", "a")]
        result = solve_order(_problem(candidates, desired=[1, 0]))
        assert result == OrderingResult((0, 1), OrderingOutcome.INFEASIBLE)


class TestDuplicateProviders:
    """Ambiguous bindings pin every statement involved to its own slot."""

    def test_two_candidates_binding_the_same_name_are_pinned(self) -> None:
        candidates = [_statement(0, "x"), _statement(1, "x"), _statement(2, "free")]
        result = solve_order(_problem(candidates, desired=[2, 1, 0]))
        assert result.order.index(0) == 0
        assert result.order.index(1) == 1

    def test_candidate_shadowing_an_anchor_binding_is_pinned(self) -> None:
        anchors = [_statement(0, "impl")]
        candidates = [_statement(1, "impl"), _statement(2, "free")]
        result = solve_order(_problem(candidates, desired=[2, 1], anchors=anchors, slots=[1, 2]))
        assert result.order == (1, 2)


class TestIsSafe:
    """The result DTO reports safety without the caller re-deriving anything."""

    def test_unconstrained_and_repaired_are_safe(self) -> None:
        unconstrained = solve_order(_problem([_statement(0, "a")], desired=[0]))
        assert unconstrained.is_safe is True

        candidates = [_statement(0, "_helper"), _statement(1, "pub", "_helper")]
        assert solve_order(_problem(candidates, desired=[1, 0])).is_safe is True
