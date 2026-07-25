# Dependency safety

Group order is not the only thing that decides where a definition may go. Anything a
statement reads *while it executes* must already be defined, so funcsort will not move a
definition below code that depends on it:

```python
def _make_strategy(): ...


@given(_make_strategy())  # runs at import time
def test_thing(value): ...
```

Here `test_thing` is public and `_make_strategy` is protected, so grouping alone would
hoist the test above the helper and the file would raise `NameError` on import. funcsort
keeps the helper first and sorts everything else normally — only the members actually
involved in a dependency give up their preferred position.

## What counts as a load-time reference

| Eager (constrains ordering) | Lazy (does not) |
| --- | --- |
| Decorator expressions, including call arguments | Function and method bodies |
| Parameter default values | Lambda bodies |
| Assignment right-hand sides | String annotations (`"Foo"`) |
| Class bases, keywords and class-body statements | `type X = ...` alias values |
| Annotations, unless the module uses `from __future__ import annotations` | |

Because bodies are lazy, mutually recursive functions still sort freely.

## When a block cannot be ordered

If a block genuinely cannot be ordered safely, funcsort leaves that block untouched and
warns rather than emitting code that does not run.

## Turning it off

Set `respect_dependencies = false` in the configuration (or pass
`--no-respect-dependencies`) to sort by group order alone. This can break imports.
