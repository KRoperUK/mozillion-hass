# Mozillion Home Assistant Custom Component

<p align="center">
  <img src="https://brands.home-assistant.io/mozillion/icon.png" alt="Mozillion Logo" width="200"/>
</p>

Tracks your [Mozillion](https://www.mozillion.com/) mobile data usage in Home
Assistant: how much you have used, how much is left, and whether the plan is
unlimited.

> This is an unofficial integration and is not affiliated with Mozillion.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=KRoperUK&repository=mozillion-hass)

### HACS (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (three dots menu) → Custom repositories
   - Add `https://github.com/KRoperUK/mozillion-hass` as repository
   - Select "Integration" as category
3. Click "Explore & Download Repositories" and search for "Mozillion"
4. Click "Download" and restart Home Assistant
5. Go to Settings → Devices & Services → Add Integration
6. Search for "Mozillion" and follow the configuration steps

### Manual Installation

1. Download the latest release from GitHub
2. Copy the `custom_components/mozillion` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "Mozillion" and follow the configuration steps

## Features

- Config flow that reads your SIMs from the dashboard and lets you pick one
- Automated login with email/password and optional TOTP secret (accepts the
  Base32 secret or the whole `otpauth://` link)
- Manual cookie + XSRF token mode if you would rather not store a password
- Four sensors — usage, total, remaining and percentage — plus raw payload and
  per-bucket attributes
- Unlimited plan binary sensor
- Transparent re-authentication, with reauth and reconfigure flows
- Redacted diagnostics downloads

Entities are named `Usage`, `Total`, `Remaining`, `Usage Percentage` and
`Unlimited` under a **Mozillion \<your SIM number\>** device.

## Configuration

Add the integration and choose one of:

- **Email, password and (if enabled) TOTP secret** — the integration logs in and
  keeps the session fresh by itself. Recommended.
- **A session cookie** copied from your browser — simplest to set up, but you
  will be asked for a fresh cookie when it expires.

You then pick your SIM from a dropdown. The scan interval defaults to one hour
and is configurable through the integration's options.

Two blueprints ship with the integration — **data running low** and **overspend limit
reached** — importable from *Settings → Automations & Scenes → Blueprints*.

Full details: [installation](docs/installation.md) ·
[use cases, entities and examples](docs/index.md) ·
[troubleshooting](docs/TROUBLESHOOTING.md).

## How it works

Mozillion regenerates usage server-side, so each poll triggers a refresh and
then polls the status endpoint until it reports `success` — the same handshake
the website's own refresh button performs.

## Development

1. Create a virtual env and install dev deps:
   ```bash
   uv sync --dev
   source .venv/bin/activate
   ```
2. Run the checks (this is what CI runs):
   ```bash
   make check   # ruff lint + format check
   make test    # pytest with the HA test harness
   ```
3. For a quick HA dev instance, use Docker Compose:
   ```bash
   docker compose -f docker-compose.dev.yml up -d
   ```
   Home Assistant UI will be at http://localhost:8123. An example config lives in
   `example-config/` and the custom component is bind-mounted.
4. To verify against the real API (needs credentials in `.env`, see
   `scripts/live_check.py`):
   ```bash
   make test-live
   ```

## Notes

- Enable debug logs if needed:
  ```yaml
  logger:
    default: warning
    logs:
      custom_components.mozillion: debug
  ```
- `MOZILLION_2FA` in `.env` accepts either a Base32 secret or an `otpauth://`
  link for the live check.
