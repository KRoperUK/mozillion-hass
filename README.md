# Mozillion Home Assistant Custom Component

<p align="center">
  <img src="https://brands.home-assistant.io/mozillion/icon.png" alt="Mozillion Logo" width="200"/>
</p>

This repository contains a Home Assistant custom component that fetches Mozillion data usage.

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
- Config flow for easy setup
- Supports manual cookies + XSRF token
- Supports automated login with email/password and optional TOTP secret
- Two sensors: usage and remaining, plus raw payload attribute

## Development
1. Create a virtual env and install dev deps:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r dev.requirements.txt
   ```
2. For a quick HA dev instance, use Docker Compose:
   ```bash
   docker compose -f docker-compose.dev.yml up -d
   ```
   Home Assistant UI will be at http://localhost:8123. An example config lives in `example-config/` and the custom component is bind-mounted.

## Usage in Home Assistant
- Option A (auto-login): Provide email, password, and TOTP secret (Base32) if applicable. Leave cookie/XSRF fields blank; the integration logs in and handles 2FA.
- Option B (manual cookies): Paste the full Cookie header (including mozillion_session, XSRF-TOKEN, etc.) and the decoded XSRF token for the header. Leave email/password blank.
- Provide your `order_detail_id` and adjust dotted JSON keys if the defaults (`data_usage`, `data_remaining`) differ.

## Notes
- CSRF token from the XSRF-TOKEN cookie must be URL-decoded when sent as the X-XSRF-TOKEN header. The integration handles this automatically when it logs in.
- Enable debug logs in HA if needed:
  ```yaml
  logger:
    default: warning
    logs:
      custom_components.mozillion: debug
  ```
