setup:
	uv sync
dev:
	uv run python -m crX
lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run ty check
type-check:
	uv run ty check
test:
	uv run pytest
test-emulator:
	uv run pytest -x -s --integration --emulator mumu
build-msi:
	uv run --group build scripts/setup_msi.py bdist_msi --target-version 0.1.0
build-dmg:
	uv run --group build scripts/setup_macos.py --target-version 0.1.0
