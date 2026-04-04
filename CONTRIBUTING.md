# Contributing to agora-code

Thank you for your interest in contributing. Here's what you need to know.

## Getting started

```bash
git clone https://github.com/thebnbrkr/agora-code
cd agora-code
pip install -e ".[dev]"
```

## How to contribute

1. **Open an issue first** for anything non-trivial — bugs, features, or design changes. This avoids duplicate work.
2. **Fork the repo** and create a branch from `main`.
3. **Make your changes** with tests where applicable.
4. **Run the test suite** before opening a PR:
   ```bash
   pytest
   ```
5. **Open a pull request** against `main` with a clear description of what changed and why.

## What to work on

Check the [ROADMAP.md](ROADMAP.md) for planned work. Issues labeled `good first issue` are good starting points.

## Code style

- Python 3.10+
- Follow existing patterns in the file you're editing
- Keep functions focused — small, single-purpose
- No docstrings required unless the logic is non-obvious

## Commit messages

Use the conventional format:
```
type: short description

feat: add call-site indexing to symbol_notes
fix: handle missing project_id in inject
docs: update install steps
```

## Reporting bugs

Use [GitHub Issues](https://github.com/thebnbrkr/agora-code/issues). Include:
- OS and Python version
- `agora-code --version` output
- Steps to reproduce
- What you expected vs. what happened

## Questions

Open a [GitHub Discussion](https://github.com/thebnbrkr/agora-code/discussions) for anything that isn't a bug or feature request.
