# funcsort

A Python tool that automatically sorts class methods and module-level functions into
configurable, regex-matched groups.

funcsort ships with a default configuration that reproduces its classic behaviour —
sorting class methods by visibility (creational → dunder → public → protected → private)
and type (instance → class → static) — but the engine underneath is fully generic: you
define your own ordered **groups**, match member names with **regular expressions**, and
control sorting at both **class and module** scope.

📖 **[Documentation](https://herogold.github.io/funcsort/)**

## Features

- Generic, configuration-driven engine with a behaviour-preserving default
- Dependency-safe: never moves a definition above code that reads it at import time
- Define your own groups and ordering; match member names with regex
- Sort both class methods **and** module-level functions
- Optionally sort module-level assignments/constants by opting a group into them
- Per-group filters by member kind, method type and scope
- Configurable via a dedicated `funcsort.toml` or `[tool.funcsort]` in `pyproject.toml`
- Pre-commit hook integration, colored output, check mode (CI) and diff mode

## Installation

```bash
# Using uv (recommended)
uv add funcsort

# Using pip
pip install funcsort

# For development
git clone https://github.com/HEROgold/funcsort
cd funcsort
uv sync
```

## Configuration

funcsort reads configuration from a dedicated `funcsort.toml` if present, otherwise
from the `[tool.funcsort]` table of `pyproject.toml`. Both use the same `[tool.funcsort]`
section and keys.

```toml
[tool.funcsort]
# Method type ordering within each group (secondary sort, optional)
# Options: "instance", "class", "static"   Default: ["instance", "class", "static"]
method_type_order = ["instance", "class", "static"]

# Sort module-level functions too (default: true)
sort_module = true

# Avoids moving definitions that could raise NameErrors or break code dependencies
# (default: true). See the docs on dependency safety:
# https://herogold.github.io/funcsort/dependency-safety/
respect_dependencies = true

# Exclude files/directories matching these glob patterns (optional)
# exclude = ["tests/*", "migrations/*.py"]
```

With **no configuration**, funcsort uses its built-in default groups. For full control,
define an ordered list of `[[tool.funcsort.groups]]`; this **replaces** the built-in
groups entirely, and the list order is the output order:

```toml
# Sort module-level UPPER_CASE constants to the very top.
[[tool.funcsort.groups]]
name = "constants"
match = "^[A-Z][A-Z0-9_]*$"
kind = ["assignment"]   # opt this group into assignments
scope = "module"        # only at module scope

# Catch-all so nothing is ever "unmatched".
[[tool.funcsort.groups]]
name = "everything_else"
match = ".*"
```

Every key, the built-in default groups and the per-group filters are documented under
[Configuration](https://herogold.github.io/funcsort/configuration/).

## Usage

```bash
# Sort a single file, several files, or a directory (recursive by default)
funcsort example.py
funcsort file1.py file2.py
funcsort src/

# Non-recursive directory sorting
funcsort src/ --no-recursive

# Check if files need sorting (useful for CI), and show a diff
funcsort --check src/
funcsort --diff example.py

# Sort class methods only, leaving module-level functions untouched
funcsort --no-sort-module src/

# Sort by group order alone, ignoring load-time dependencies (can break imports)
funcsort --no-respect-dependencies src/

# Exclude specific files or directories
funcsort --exclude "tests/*" --exclude "migrations/*.py" src/
```

**Note**: By default, funcsort excludes all dot-prefixed directories (e.g. `.venv`,
`.git`, `.pytest_cache`) and common build directories (`venv`, `__pycache__`,
`node_modules`) when scanning directories recursively. You can add custom exclusions via
CLI flags or the config file.

Individual files, classes and members can opt out of sorting with a `# nosort` comment —
see [Skipping sorting](https://herogold.github.io/funcsort/nosort/).

## Pre-commit Integration

Add to your `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: funcsort
        name: funcsort
        entry: funcsort
        language: python
        types: [python]
        additional_dependencies: ["funcsort"]
```

Then install the hook:

```bash
pip install pre-commit
pre-commit install
```

## Documentation

- [Configuration](https://herogold.github.io/funcsort/configuration/) — every key, the
  default groups, and the per-group filters
- [Sorting behaviour](https://herogold.github.io/funcsort/sorting-behaviour/) — the
  two-level sort and the minimise-movement rule
- [Dependency safety](https://herogold.github.io/funcsort/dependency-safety/) — why a
  definition is never moved below code that reads it at import time
- [Skipping sorting](https://herogold.github.io/funcsort/nosort/) — the `# nosort` escape hatches
- [Example](https://herogold.github.io/funcsort/example/) — a class before and after sorting
- [API Reference](https://herogold.github.io/funcsort/reference/sorter/) — the public Python API

## Development

```bash
# Install dependencies
uv sync

# Run on example file
uv run funcsort example.py

# Test with check mode
uv run funcsort --check example.py

# View diff
uv run funcsort --diff example.py

# Build the documentation site locally
uv run --group docs mkdocs serve
```

## License

MIT
