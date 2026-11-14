---
description: 'Python conventions for this repo'
applyTo: 'kubed/**/*.py,scripts/**/*.py,tests/**/*.py'
---
# Python conventions

The rules are in [CONTRIBUTING.md](../../CONTRIBUTING.md), "Writing code here" (tool
declarations, coercion, failures, secrets and logs, Python style) and "Tests". Flag a violation of
any of them.

- The supported range is 3.11 to 3.14: a 3.12+ feature breaks the 3.11 leg. Raising
  `requires-python` is a pull request of its own.
- `ruff check .` covers the whole checkout, tests included; `tests/*` only ignores `E501`.
