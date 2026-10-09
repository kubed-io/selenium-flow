## In this repository

The checks a change passes here are CI's: `ruff check .`, `ruff format --check .`,
`pytest` (the integration tests need a Grid and run in CI, not here), the UI build
(`npm --prefix ui run build`) when `ui/` changed, and a new entry under
`## [Unreleased]` in `CHANGELOG.md`. Copilot reviews against
`.github/copilot-instructions.md` and `.github/instructions/`, so write to them.
