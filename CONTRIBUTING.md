# Contributing to Mozillion HASS

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

1. Clone the repo and create a virtual environment:
   ```bash
   git clone https://github.com/KRoperUK/mozillion-hass.git
   cd mozillion-hass
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r dev.requirements.txt
   ```

2. Start a local Home Assistant instance:
   ```bash
   make up
   ```
   The HA UI will be at http://localhost:8123 with the custom component bind-mounted.

## Commit Convention

This project uses [Conventional Commits](https://www.conventionalcommits.org/):

- `feat:` — new features
- `fix:` — bug fixes
- `chore:` — maintenance (deps, CI, docs)
- `refactor:` — code changes that don't fix bugs or add features

Examples:
```
feat: add daily data reset sensor
fix: handle expired session cookies gracefully
chore: bump pyotp to 2.10.0
```

## Branch Strategy

1. Create a feature branch from `main`
2. Make your changes with conventional commit messages
3. Open a PR against `main`
4. CI will run linting and HACS validation automatically

## Linting

```bash
pip install ruff
ruff check custom_components/
ruff format custom_components/
```

## Useful Make Targets

| Command        | Description                  |
| -------------- | ---------------------------- |
| `make up`      | Start dev HA instance        |
| `make down`    | Stop dev HA instance         |
| `make logs`    | Tail HA logs                 |
| `make restart` | Restart dev HA instance      |
