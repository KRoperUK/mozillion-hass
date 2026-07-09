# AGENTS.md

Guidance for AI coding agents working in this repository.

## What this is

A **Home Assistant custom integration** (`custom_components/mozillion`) that
fetches Mozillion data usage via the Mozillion API. Distributed via HACS.
`iot_class` is `cloud_polling`.

## Architecture

| File | Responsibility |
| --- | --- |
| `__init__.py` | Entry setup/unload. Creates coordinator, classifies errors into `ConfigEntryNotReady` (transient) vs `ConfigEntryAuthFailed` (reauth). |
| `api.py` | `MozillionAPI` — handles authentication (cookie-based or email/password+TOTP), session management, and data fetching. |
| `coordinator.py` | `MozillionCoordinator(DataUpdateCoordinator)` — polls the API, handles expired sessions with transparent re-authentication. |
| `config_flow.py` | Two-phase config flow: user enters credentials (email/password+TOTP or cookie/XSRF), validates against the API. |
| `sensor.py` | Builds `MozillionSensor` entities from coordinator data — usage and remaining, plus raw payload attribute. |
| `binary_sensor.py` | `MozillionBinarySensor` for connectivity status. |
| `const.py` | Domain, config keys, defaults. |

## Commands

```bash
# Environment (uv)
uv sync --dev

# Lint, type-check, format, test (mirror CI)
uv run ruff check custom_components/ tests/
uv run ruff format --check custom_components/ tests/
uv run mypy custom_components/mozillion
uv run pytest tests/
```

## Conventions

- **Python 3.14** (Home Assistant >=2026.6 requires >=3.14), ruff (line length 88) for lint + format.
- **Conventional Commits** for commit and PR titles (`fix:`, `feat:`, `chore:`, `docs:`) — this drives changelog and version bumps.
- Every behaviour change needs tests. Tests mock `MozillionAPI` — see `tests/conftest.py` fixtures.
- `strings.json` and `translations/*.json` must stay in sync.

## Workflow / repo rules

- **`main` is protected**: open a feature branch and a PR; do not push to `main`.
  Required checks: `changes`, `lint`, `test`, `hacs_validate`.
- Releases are cut via `release-please` — push conventional commits to `main` and
  release-please opens/maintains a release PR. Merge it to publish.
- Pre-commit hooks: trailing whitespace, EOF, YAML/JSON/TOML validation, ruff
  lint+format, conventional commits, sort-manifest.
