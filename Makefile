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
	$(PYTHON) -m pytest tests/ --ignore=tests/test_live.py -v

test-live:
	$(PYTHON) -m pytest tests/test_live.py -v --log-cli-level=INFO

test-all:
	$(PYTHON) -m pytest tests/ -v --log-cli-level=INFO

# ── Linting & Formatting ───────────────────────────────
lint:
	ruff check custom_components/ tests/

format:
	ruff format custom_components/ tests/

check: lint
	ruff format --check custom_components/ tests/

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
	python3 -m venv .venv
	.venv/bin/pip install -r requirements_test.txt

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	rm -rf .pytest_cache
