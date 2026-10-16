# Development

Use Python 3.12 through uv. Run `uv sync --locked`; dependencies belong only in `.venv`.
Start with `uv run python -m crX`. No globally installed packages are required.

Before delivering a change, run:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest -q
```

Tests needing Android hardware are marked `emulator` and excluded by default.
Keep device selection explicit and all process calls shell-free with timeouts.
Preserve upstream copyright and license notices.
