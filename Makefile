COMPOSE_FILE := docker-compose.dev.yml
PYTHON      := .venv/bin/python

.PHONY: up down logs ps restart test test-live lint format check ci venv clean

# ── Docker ──────────────────────────────────────────────
up:
	docker compose -f $(COMPOSE_FILE) up -d

down:
	docker compose -f $(COMPOSE_FILE) down

logs:
	docker compose -f $(COMPOSE_FILE) logs -f --tail=200

ps:
	docker compose -f $(COMPOSE_FILE) ps

restart: down up

# ── Testing ─────────────────────────────────────────────
test:
	$(PYTHON) -m pytest tests/ -v

# Hits the real Mozillion API; needs MOZILLION_EMAIL/MOZILLION_PASSWORD in .env.
# Not a pytest module: the HA test plugin disables DNS for every pytest test.
test-live:
	$(PYTHON) scripts/live_check.py

test-all: test test-live

# ── Linting & Formatting ───────────────────────────────
lint:
	ruff check custom_components/ scripts/ tests/

format:
	ruff format custom_components/ tests/

check: lint
	ruff format --check custom_components/ scripts/ tests/

# ── Pre-commit ──────────────────────────────────────────
pre-commit:
	pre-commit run --all-files

# ── CI (local mirror) ───────────────────────────────────
ci: check test

# ── CI (run GitHub Actions locally via act) ─────────────
ACT := ~/.local/bin/act push -W .github/workflows/ci.yml --container-architecture linux/amd64

ci-local: ci-local-lint
	@echo "ℹ️  HACS/hassfest skipped locally (needs GHCR auth). Use 'make ci-local-full' with a GitHub token."

ci-local-lint:
	$(ACT) -j lint

ci-local-full:
	$(ACT) -s GITHUB_TOKEN="$(GITHUB_TOKEN)"

# ── Setup ───────────────────────────────────────────────
venv:
	uv sync --dev

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache
