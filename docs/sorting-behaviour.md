# Sorting behaviour

Methods are sorted in two levels:

1. **Primary**: By visibility (creational → dunder → public → protected → private by default)
2. **Secondary**: Within each visibility level, by method type (instance → class → static by default)

The sorting algorithm **minimises movement** to preserve the original order as much as
possible:

- Methods that need to move DOWN (to a later section) are placed at the **beginning** of
  their target section
- Methods that need to move UP (to an earlier section) are placed at the **end** of their
  target section
- Methods already in the correct section maintain their relative order

Example order with the default configuration:

1. Creational instance methods (`__init__`, `__new__`, …)
2. Creational class methods
3. Creational static methods
4. Dunder instance methods (`__str__`, `__eq__`, …)
5. Dunder class methods
6. Dunder static methods
7. Public instance methods
8. Public class methods
9. Public static methods
10. Protected instance methods
11. Protected class methods
12. Protected static methods
13. Private instance methods
14. Private class methods
15. Private static methods

Group order is not the only thing that decides where a definition may go — see
[Dependency safety](dependency-safety.md) for the constraints layered on top of it.

## Module functions and classes

At module level, functions are kept together on one side of the classes instead of being
split around them. `function_placement` picks the side:

- `after-classes` (default): every module-level function, public or private, goes below
  every module-level class.
- `before-classes`: every module-level function goes above every module-level class.
- `interleaved`: classes stay where they are and functions fill the gaps around them in
  group order, so public functions can rise above the classes while private ones sink
  below them.

Classes keep their relative order, and the functions keep their group order. A function
a class needs while it is defined (a class decorator, a call in the class body) stays
above that class, and a function that needs a class while it is defined (a parameter
default, say) stays below it, unless `respect_dependencies` is off. A class marked
`# nosort` never moves. Nested classes and class bodies are unaffected.
