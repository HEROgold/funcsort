# Skipping sorting with `# nosort`

You can prevent sorting at different levels using `# nosort` comments (case-insensitive).

## File level

Skip the entire file:

```python
# nosort: file
class Example:
    def _protected(self):
        pass
    def public(self):
        pass  # File won't be sorted
```

## Class level

Skip a specific class:

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

## Member level

Keep a member in its current position:

```python
class Example:
    def public_a(self):
        pass

    def _protected(self):  # nosort
        pass  # Stays here, between public methods

    def public_b(self):
        pass  # Will move up, but _protected stays in place
```
