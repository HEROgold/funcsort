# Configuration

funcsort reads configuration from a dedicated `funcsort.toml` if present, otherwise from
the `[tool.funcsort]` table of `pyproject.toml`. Both use the same `[tool.funcsort]`
section and keys.

## Scalar settings

With **no configuration**, funcsort uses its built-in default groups (creational →
dunder → public → protected → private, each split by instance → class → static). To
customise the ordering you define your own groups (below); the scalar settings tune the
rest:

```toml
[tool.funcsort]
# Method type ordering within each group (secondary sort, optional)
# Options: "instance", "class", "static"   Default: ["instance", "class", "static"]
method_type_order = ["instance", "class", "static"]

# Sort module-level functions too (default: true)
sort_module = true

# Avoids moving definitions that could raise NameErrors or break code dependencies
# (default: true). Turning this off can break imports.
respect_dependencies = true

# Exclude files/directories matching these glob patterns (optional)
# exclude = ["tests/*", "migrations/*.py"]
```

See [Dependency safety](dependency-safety.md) for what `respect_dependencies` actually
analyses.

## Custom groups (full control)

For full control, define an ordered list of `[[tool.funcsort.groups]]`. This **replaces**
the built-in groups entirely. Each group matches member names by regex (first-match-wins
down the list); the list order is the output order.

```toml
[tool.funcsort]
method_type_order = ["instance", "class", "static"]

# Sort module-level UPPER_CASE constants to the very top.
[[tool.funcsort.groups]]
name = "constants"
match = "^[A-Z][A-Z0-9_]*$"
kind = ["assignment"]   # opt this group into assignments
scope = "module"        # only at module scope

# Group pytest-style fixtures next, in classes only.
[[tool.funcsort.groups]]
name = "fixtures"
match = "^(setup|teardown)"
scope = "class"

# Then magic methods.
[[tool.funcsort.groups]]
name = "dunder"
match = "^__.+__$"

# Catch-all so nothing is ever "unmatched".
[[tool.funcsort.groups]]
name = "everything_else"
match = ".*"
```

Each group table accepts:

- `name` (required) — identifier used in diagnostics.
- `match` (required) — a regex string or a list of strings (matched if **any** matches).
  A bare identifier (e.g. `"__init__"`) is treated as an exact-name match.
- `kind` (optional) — `"function"` (default) and/or `"assignment"`. A group must opt into
  `"assignment"` for constants/assignments to be sorted; otherwise they stay anchored.
- `type` (optional) — restrict to `"instance"`, `"class"` and/or `"static"`.
- `scope` (optional) — restrict to `"class"` and/or `"module"`.
- `decorator` (optional) — a regex/exact string or list; the member must carry a decorator
  whose dotted name (calls stripped, e.g. `app.route` from `@app.route("/x")`) matches one.

!!! note "Unmatched members"

    With custom groups, a member that matches no group is moved to the end of its block
    (preserving relative order) and reported with a warning. Add a `".*"` catch-all group
    to collect them where you want.

## Default group rules

The built-in default groups classify member names as:

- **Creational**: Lifecycle dunders (`__new__`, `__init__`, `__init_subclass__`,
  `__post_init__`, `__set_name__`); to change this set, define your own `creational` group.
- **Dunder**: Any other magic method (e.g. `__str__`, `__repr__`, `__eq__`, `__get__`)
- **Public**: No underscore prefix (e.g. `def method()`)
- **Protected**: Single underscore prefix (e.g. `def _method()`)
- **Private**: Double underscore prefix, not magic (e.g. `def __method()`)

## Method type rules

- **Class methods**: Decorated with `@classmethod`
- **Static methods**: Decorated with `@staticmethod`
- **Instance methods**: Regular methods (no special decorator)
