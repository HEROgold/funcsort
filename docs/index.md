# funcsort

A Python tool that automatically sorts class methods and module-level functions into
configurable, regex-matched groups.

funcsort ships with a default configuration that reproduces its classic behaviour —
sorting class methods by visibility (creational → dunder → public → protected → private)
and type (instance → class → static) — but the engine underneath is fully generic: you
define your own ordered **groups**, match member names with **regular expressions**, and
control sorting at both **class and module** scope.

## Installation

```bash
# Using uv (recommended)
uv add funcsort

# Using pip
pip install funcsort
```

## Where to go next

- [Configuration](configuration.md) — every key of `funcsort.toml` / `[tool.funcsort]`,
  the built-in default groups, and how to define your own.
- [Sorting behaviour](sorting-behaviour.md) — the two-level sort and the
  minimise-movement rule that decides where a member lands.
- [Dependency safety](dependency-safety.md) — why a definition is never moved below code
  that reads it at import time, and what counts as a load-time reference.
- [Skipping sorting](nosort.md) — the `# nosort` escape hatches, per file, class or member.
- [Example](example.md) — a class before and after sorting with the default config.
- [API Reference](reference/sorter.md) — the public Python API.
