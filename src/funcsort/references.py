"""Load-time name analysis: what a statement binds, and what it reads while executing.

This module is the second libcst adapter (alongside :mod:`funcsort.sorter`); the pure
constraint model it feeds lives in :mod:`funcsort.ordering`.

Reordering statements is only safe if every name a statement reads *at the moment it
executes* is already bound. Decorator expressions, parameter defaults and assignment
right-hand sides run immediately; function bodies do not. That distinction is the whole
job of this module: it separates **eager** references (which constrain ordering) from
**lazy** ones (which never do, or mutual recursion would freeze every block).

The governing invariant:

    Over-collecting ``provides`` or ``requires`` is safe -- it only costs sorting.
    Under-collecting ``requires`` emits broken code.

Every ambiguity below is therefore resolved toward over-collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import override

import libcst as cst

_FUTURE_MODULE = "__future__"
_ANNOTATIONS_FEATURE = "annotations"


@dataclass(frozen=True)
class NameFlow:
    """Names a statement binds, and the names it reads when it executes.

    Attributes:
        provides: Names bound in the enclosing scope by this statement.
        requires: Free names read eagerly, i.e. while the statement itself runs.

    """

    provides: frozenset[str] = frozenset()
    requires: frozenset[str] = frozenset()

    def merge(self, other: NameFlow) -> NameFlow:
        """Return the union of two flows (used for multi-statement lines and blocks)."""
        return NameFlow(self.provides | other.provides, self.requires | other.requires)


EMPTY_FLOW = NameFlow()


def name_flow(node: cst.CSTNode, *, lazy_annotations: bool = False) -> NameFlow:
    """Return the :class:`NameFlow` of a single block-level statement.

    Args:
        node: A statement from a module or class body.
        lazy_annotations: Whether annotations are deferred (``from __future__ import
            annotations``), in which case they contribute no eager references.

    Returns:
        The names the statement binds and the names it needs at execution time.

    """
    return NameFlow(
        provides=frozenset(_bound_names(node)),
        requires=frozenset(_free_names(node, lazy_annotations=lazy_annotations)),
    )


def uses_future_annotations(module: cst.Module) -> bool:
    """Return whether the module defers annotation evaluation via a ``__future__`` import."""
    for line in module.body:
        if not isinstance(line, cst.SimpleStatementLine):
            continue
        for statement in line.body:
            if not isinstance(statement, cst.ImportFrom) or isinstance(statement.names, cst.ImportStar):
                continue
            if _dotted_name(statement.module) != _FUTURE_MODULE:
                continue
            if any(alias.name.value == _ANNOTATIONS_FEATURE for alias in statement.names):
                return True
    return False


def _dotted_name(node: cst.BaseExpression | None) -> str:
    """Normalise an attribute/name chain to its dotted form, or ``""`` for anything else."""
    parts: list[str] = []
    while isinstance(node, cst.Attribute):
        parts.append(node.attr.value)
        node = node.value
    if isinstance(node, cst.Name):
        parts.append(node.value)
    return ".".join(reversed(parts))


def _root_name(node: cst.BaseExpression | None) -> str:
    """Return the leftmost component of an attribute chain (``import a.b`` binds ``a``)."""
    return _dotted_name(node).split(".", 1)[0]


class _BoundNames(cst.CSTVisitor):
    """Collect the names a statement binds in its *enclosing* scope.

    Descends through compound statements (an ``if TYPE_CHECKING:`` block really does bind
    its imports at module level) but never into a function body, whose bindings are local.
    """

    def __init__(self) -> None:
        """Start with an empty binding set."""
        self.names: set[str] = set()

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Bind the function name; its body binds only locals."""
        self.names.add(node.name.value)
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        """Bind the class name; its body binds class attributes, not enclosing names."""
        self.names.add(node.name.value)
        return False

    @override
    def visit_Lambda(self, node: cst.Lambda) -> bool:
        """Skip a lambda: it binds nothing in the enclosing scope."""
        return False

    @override
    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool:
        """Bind every name reachable through a (possibly destructuring) assignment target."""
        self._bind_target(node.target)
        return False

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        """Bind an annotated target only when it actually has a value.

        A bare ``x: int`` is a declaration; at runtime it binds nothing, and pretending
        otherwise would create a spurious second provider for ``x``.
        """
        if node.value is not None:
            self._bind_target(node.target)
        return False

    @override
    def visit_AugAssign(self, node: cst.AugAssign) -> bool:
        """Bind the target of an augmented assignment, which rebinds it."""
        self._bind_target(node.target)
        return False

    @override
    def visit_NamedExpr(self, node: cst.NamedExpr) -> bool:
        """Bind a walrus target, which lands in the enclosing scope."""
        self._bind_target(node.target)
        return True

    @override
    def visit_Import(self, node: cst.Import) -> bool:
        """``import a.b`` binds ``a``; ``import a.b as c`` binds ``c``."""
        for alias in node.names:
            self.names.add(_alias_name(alias) or _root_name(alias.name))
        return False

    @override
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        """``from x import y as z`` binds ``z``; ``import *`` is treated as binding nothing."""
        if isinstance(node.names, cst.ImportStar):
            # An unknown binding set. Claiming names would be guesswork; claiming none only
            # risks attributing a name to a same-block definition, which over-constrains.
            return False
        for alias in node.names:
            self.names.add(_alias_name(alias) or _root_name(alias.name))
        return False

    @override
    def visit_For(self, node: cst.For) -> bool:
        """Bind the loop target, which lands in the enclosing scope."""
        self._bind_target(node.target)
        return True

    @override
    def visit_AsName(self, node: cst.AsName) -> bool:
        """``with ... as x`` and ``except ... as e`` bind their names."""
        if isinstance(node.name, cst.BaseExpression):  # pyright: ignore[reportUnnecessaryIsInstance]
            self._bind_target(node.name)
        return False

    @override
    def visit_ExceptHandler(self, node: cst.ExceptHandler) -> bool:
        """Bind the exception alias, then continue into the handler body."""
        if node.name is not None:
            self.visit_AsName(node.name)
        return True

    @override
    def visit_MatchAs(self, node: cst.MatchAs) -> bool:
        """Bind the name captured by a match pattern."""
        if node.name is not None:
            self.names.add(node.name.value)
        return True

    @override
    def visit_MatchStar(self, node: cst.MatchStar) -> bool:
        """Bind the name captured by a starred match pattern."""
        if node.name is not None:
            self.names.add(node.name.value)
        return True

    @override
    def visit_TypeAlias(self, node: cst.TypeAlias) -> bool:
        """``type X = ...`` binds ``X``; the value is lazily evaluated (PEP 695)."""
        self.names.add(node.name.value)
        return False

    def _bind_target(self, target: cst.BaseExpression) -> None:
        """Bind every ``Name`` reachable through a destructuring target."""
        if isinstance(target, cst.Name):
            self.names.add(target.value)
        elif isinstance(target, cst.Tuple | cst.List):
            for element in target.elements:
                self._bind_target(element.value)
        elif isinstance(target, cst.StarredElement):
            self._bind_target(target.value)
        # Attribute/Subscript targets mutate an existing object; they bind no new name.


class _FreeNames(cst.CSTVisitor):
    """Collect the names a statement reads *eagerly*.

    Two failure modes matter, and they are not symmetric. Missing a reference emits code
    that raises ``NameError``; inventing one merely blocks a reorder. So the visitor
    defaults to recursing, and only carves out cases that are provably not references
    (attribute labels, keyword argument names, parameter names) or provably deferred
    (function and lambda bodies, string annotations).
    """

    def __init__(self, *, lazy_annotations: bool) -> None:
        """Start with an empty reference set."""
        self.lazy_annotations = lazy_annotations
        self.names: set[str] = set()

    @override
    def visit_Name(self, node: cst.Name) -> bool:
        """Record a bare name reference."""
        self.names.add(node.value)
        return False

    @override
    def visit_Attribute(self, node: cst.Attribute) -> bool:
        """``a.b.c`` references only ``a``; ``b`` and ``c`` are attribute labels."""
        node.value.visit(self)
        return False

    @override
    def visit_Arg(self, node: cst.Arg) -> bool:
        """``f(x=1)`` references ``f`` and ``1``, never the keyword ``x``."""
        node.value.visit(self)
        return False

    @override
    def visit_Param(self, node: cst.Param) -> bool:
        """Read only a parameter's default and annotation; its name is a binding."""
        self._visit_annotation(node.annotation)
        if node.default is not None:
            node.default.visit(self)
        return False

    @override
    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        """Read the signature eagerly; the body runs later and constrains nothing.

        Decorators are visited as *whole expressions* -- ``@given(_class_source())``
        depends on ``_class_source``, which a name-only reading of the decorator misses.
        """
        for decorator in node.decorators:
            decorator.decorator.visit(self)
        node.params.visit(self)
        self._visit_annotation(node.returns)
        if node.type_parameters is not None:
            node.type_parameters.visit(self)
        return False

    @override
    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        """Recurse into a class body, which executes at definition time.

        Names bound inside the body are deliberately *not* subtracted: ``name = name``
        idioms make subtraction unsound, and keeping them only adds a harmless edge.
        """
        for decorator in node.decorators:
            decorator.decorator.visit(self)
        for base in node.bases:
            base.visit(self)
        for keyword in node.keywords:
            keyword.visit(self)
        if node.type_parameters is not None:
            node.type_parameters.visit(self)
        node.body.visit(self)
        return False

    @override
    def visit_Lambda(self, node: cst.Lambda) -> bool:
        """Lambda defaults are evaluated now; the body is not."""
        node.params.visit(self)
        return False

    @override
    def visit_Annotation(self, node: cst.Annotation) -> bool:
        """Dispatch through the shared annotation rule."""
        self._visit_annotation(node)
        return False

    @override
    def visit_AssignTarget(self, node: cst.AssignTarget) -> bool:
        """Read only what a target dereferences: ``obj.attr`` and ``d[k]`` need their base."""
        self._visit_target(node.target)
        return False

    @override
    def visit_AnnAssign(self, node: cst.AnnAssign) -> bool:
        """Read the annotation and the value; the target is written."""
        self._visit_target(node.target)
        self._visit_annotation(node.annotation)
        if node.value is not None:
            node.value.visit(self)
        return False

    @override
    def visit_AugAssign(self, node: cst.AugAssign) -> bool:
        """``x += v`` genuinely reads ``x`` as well as rebinding it."""
        node.target.visit(self)
        node.value.visit(self)
        return False

    @override
    def visit_Import(self, node: cst.Import) -> bool:
        """Skip an import: it reads nothing from the enclosing scope."""
        return False

    @override
    def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
        """Skip an import: it reads nothing from the enclosing scope."""
        return False

    @override
    def visit_TypeAlias(self, node: cst.TypeAlias) -> bool:
        """PEP 695 alias values are lazily evaluated, so they constrain nothing."""
        return False

    def _visit_target(self, target: cst.BaseExpression) -> None:
        """Read only what an assignment target dereferences.

        ``x = v`` reads nothing from ``x``, but ``obj.attr = v`` must read ``obj`` and
        ``d[k] = v`` must read both ``d`` and ``k`` -- those objects have to exist already.
        """
        if isinstance(target, cst.Name):
            return
        if isinstance(target, cst.Tuple | cst.List):
            for element in target.elements:
                self._visit_target(element.value)
            return
        if isinstance(target, cst.StarredElement):
            self._visit_target(target.value)
            return
        target.visit(self)

    def _visit_annotation(self, annotation: cst.Annotation | None) -> None:
        """Recurse into an annotation unless it is deferred.

        A string annotation is never evaluated. Non-string annotations are evaluated at
        definition time unless the module opted into PEP 563. PEP 649 (3.14) makes them
        lazy unconditionally, but funcsort cannot know the *target* interpreter of the
        file it formats, so the eager reading stays -- it is the safe over-collection.
        """
        if annotation is None or self.lazy_annotations:
            return
        if isinstance(annotation.annotation, cst.SimpleString | cst.ConcatenatedString):
            return
        annotation.annotation.visit(self)


def _alias_name(alias: cst.ImportAlias) -> str:
    """Return an import alias's ``as`` name, or ``""`` when it has none."""
    if alias.asname is None or not isinstance(alias.asname.name, cst.Name):
        return ""
    return alias.asname.name.value


def _bound_names(node: cst.CSTNode) -> set[str]:
    """Return the names ``node`` binds in its enclosing scope."""
    visitor = _BoundNames()
    node.visit(visitor)
    return visitor.names


def _free_names(node: cst.CSTNode, *, lazy_annotations: bool) -> set[str]:
    """Return the names ``node`` reads while it executes."""
    visitor = _FreeNames(lazy_annotations=lazy_annotations)
    node.visit(visitor)
    return visitor.names
