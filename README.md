# undersort

A Python tool that automatically sorts class methods and module-level functions into
configurable, regex-matched groups.

undersort ships with a default configuration that reproduces its classic behaviour —
sorting class methods by visibility (creational → dunder → public → protected → private)
and type (instance → class → static) — but the engine underneath is fully generic: you
define your own ordered **groups**, match member names with **regular expressions**, and
control sorting at both **class and module** scope.

## Features

- Generic, configuration-driven engine with a behaviour-preserving default
- Define your own groups and ordering; match member names with regex
- Sort both class methods **and** module-level functions
- Optionally sort module-level assignments/constants by opting a group into them
- Per-group filters by member kind, method type and scope
- Configurable via a dedicated `undersort.toml` or `[tool.undersort]` in `pyproject.toml`
- Pre-commit hook integration, colored output, check mode (CI) and diff mode

## Installation

```bash
# Using uv (recommended)
uv add undersort

# Using pip
pip install undersort

# For development
git clone https://github.com/kivicode/undersort
cd undersort
uv sync
```

## Configuration

undersort reads configuration from a dedicated `undersort.toml` if present, otherwise
from the `[tool.undersort]` table of `pyproject.toml`. Both use the same `[tool.undersort]`
section and keys.

### Simple configuration (classic ordering)

For the classic visibility-based ordering, just set `order` (and optionally
`method_type_order`):

```toml
[tool.undersort]
# Visibility ordering (primary sort)
# Options: "creational", "dunder", "public", "protected", "private"
# Default: ["creational", "dunder", "public", "protected", "private"]
order = ["creational", "dunder", "public", "protected", "private"]

# Method type ordering within each group (secondary sort, optional)
# Options: "instance", "class", "static"   Default: ["instance", "class", "static"]
method_type_order = ["instance", "class", "static"]

# Override which dunders count as "creational" (optional)
# Default: ["__new__", "__init__", "__init_subclass__", "__post_init__", "__set_name__"]
# creational_dunders = ["__new__", "__init__", "__enter__"]

# Sort module-level functions too (default: true)
sort_module = true

# Exclude files/directories matching these glob patterns (optional)
# exclude = ["tests/*", "migrations/*.py"]
```

### Custom groups (full control)

For full control, define an ordered list of `[[tool.undersort.groups]]`. This **replaces**
the built-in groups entirely. Each group matches member names by regex (first-match-wins
down the list); the list order is the output order.

```toml
[tool.undersort]
method_type_order = ["instance", "class", "static"]

# Sort module-level UPPER_CASE constants to the very top.
[[tool.undersort.groups]]
name = "constants"
match = "^[A-Z][A-Z0-9_]*$"
kind = ["assignment"]   # opt this group into assignments
scope = "module"        # only at module scope

# Group pytest-style fixtures next, in classes only.
[[tool.undersort.groups]]
name = "fixtures"
match = "^(setup|teardown)"
scope = "class"

# Then magic methods.
[[tool.undersort.groups]]
name = "dunder"
match = "^__.+__$"

# Catch-all so nothing is ever "unmatched".
[[tool.undersort.groups]]
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

> **Unmatched members**: with custom groups, a member that matches no group is moved to the
> end of its block (preserving relative order) and reported with a warning. Add a `".*"`
> catch-all group to collect them where you want.

### Method Visibility Rules

- **Creational methods**: Lifecycle dunders that construct/initialize an object or class
  (`__new__`, `__init__`, `__init_subclass__`, `__post_init__`, `__set_name__` by default;
  configurable via `creational_dunders`)
- **Dunder methods**: Any other magic method (e.g., `__str__`, `__repr__`, `__eq__`, `__get__`)
- **Public methods**: No underscore prefix (e.g., `def method()`)
- **Protected methods**: Single underscore prefix (e.g., `def _method()`)
- **Private methods**: Double underscore prefix, not magic (e.g., `def __method()`)

> **Backward compatibility**: If `order` omits `"creational"` and `"dunder"` (e.g. you keep
> `order = ["public", "protected", "private"]`), magic methods are treated as `public` — the
> pre-existing behavior — so they are never dropped.

### Method Type Rules

- **Class methods**: Decorated with `@classmethod`
- **Static methods**: Decorated with `@staticmethod`
- **Instance methods**: Regular methods (no special decorator)

### Sorting Behavior

Methods are sorted in two levels:

1. **Primary**: By visibility (creational → dunder → public → protected → private by default)
2. **Secondary**: Within each visibility level, by method type (instance → class → static by default)

The sorting algorithm **minimizes movement** to preserve the original order as much as possible:

- Methods that need to move DOWN (to a later section) are placed at the **beginning** of their target section
- Methods that need to move UP (to an earlier section) are placed at the **end** of their target section
- Methods already in the correct section maintain their relative order

Example order with default configuration:

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

### Skipping Sorting with `# nosort`

You can prevent sorting at different levels using `# nosort` comments (case-insensitive):

**File-level**: Skip entire file

```python
# nosort: file
class Example:
    def _protected(self):
        pass
    def public(self):
        pass  # File won't be sorted
```

**Class-level**: Skip specific class

```python
class Example:  # nosort
    def _protected(self):
        pass
    def public(self):
        pass  # This class won't be sorted

class Other:
    def _protected(self):
        pass
    def public(self):
        pass  # This class WILL be sorted
```

**Method-level**: Keep method in its current position

```python
class Example:
    def public_a(self):
        pass

    def _protected(self):  # nosort
        pass  # Stays here, between public methods

    def public_b(self):
        pass  # Will move up, but _protected stays in place
```

## Usage

### Command Line

```bash
# Sort a single file
undersort example.py

# Sort multiple files
undersort file1.py file2.py file3.py

# Sort all Python files in a directory (recursive by default)
undersort src/

# Sort all Python files in current directory and subdirectories
undersort .

# Non-recursive directory sorting (only files in the directory, not subdirectories)
undersort src/ --no-recursive

# Wildcards work too (expanded by shell)
undersort *.py
undersort src/**/*.py

# Check if files need sorting (useful for CI)
undersort --check example.py
undersort --check src/

# Show diff of changes
undersort --diff example.py

# Sort class methods only, leaving module-level functions untouched
undersort --no-sort-module src/

# Combine flags
undersort --check --diff src/

# Exclude specific files or directories
undersort --exclude "tests/*" --exclude "migrations/*.py" src/

# Multiple exclude patterns (can be combined with config file patterns)
undersort --exclude "test_*.py" --exclude "*/legacy/*" .
```

**Note**: By default, undersort excludes all dot-prefixed directories (e.g., `.venv`, `.git`, `.pytest_cache`) and common build directories (`venv`, `__pycache__`, `node_modules`) when scanning directories recursively. You can add custom exclusions via CLI flags or the config file.

### Pre-commit Integration

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: undersort
        name: undersort
        entry: undersort
        language: python
        types: [python]
        additional_dependencies: ["undersort"]
```

Then install the hook:

```bash
pip install pre-commit
pre-commit install
```

## Example

### Before

```python
class Example:
    def _protected_instance(self):
        pass

    @staticmethod
    def public_static():
        pass

    def __init__(self):
        pass

    @classmethod
    def _protected_class(cls):
        pass

    def public_instance(self):
        pass

    def __private_method(self):
        pass

    @classmethod
    def public_class(cls):
        pass
```

### After (with default config)

```python
class Example:
    def __init__(self):
        pass

    def public_instance(self):
        pass

    @classmethod
    def public_class(cls):
        pass

    @staticmethod
    def public_static():
        pass

    def _protected_instance(self):
        pass

    @classmethod
    def _protected_class(cls):
        pass

    def __private_method(self):
        pass
```

The methods are now organized by:

1. **Visibility**: creational (`__init__`) → dunder → public → protected → private
2. **Type** (within each visibility): instance → class → static

## Development

```bash
# Install dependencies
uv sync

# Run on example file
uv run undersort example.py

# Test with check mode
uv run undersort --check example.py

# View diff
uv run undersort --diff example.py
```

## License

MIT
