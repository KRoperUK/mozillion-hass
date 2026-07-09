# Installation & Setup

> **Prerequisites:** A [Mozillion](https://www.mozillion.com/) account with an active mobile plan.

## HACS (Recommended)

1. Make sure you have [HACS](https://hacs.xyz/) installed in your Home Assistant instance
2. Add this repository as a custom repository in HACS:
   - Go to HACS → Integrations → ⋮ (three dots menu) → Custom repositories
   - Add `https://github.com/KRoperUK/mozillion-hass` as repository
   - Select "Integration" as category
3. Click "Explore & Download Repositories" and search for "Mozillion"
4. Click "Download" and restart Home Assistant
5. Go to Settings → Devices & Services → Add Integration
6. Search for "Mozillion" and follow the configuration steps

## Manual Installation

1. Download the latest release from [GitHub Releases](https://github.com/KRoperUK/mozillion-hass/releases)
2. Copy the `custom_components/mozillion` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "Mozillion" and follow the configuration steps

## Configuration

### Option A — Auto-login

Provide your Mozillion **email**, **password**, and optional **TOTP secret** (Base32) if two-factor authentication is enabled. Leave the cookie and XSRF fields blank; the integration logs in and handles 2FA automatically.

### Option B — Manual cookies

1. Log into Mozillion in your browser
2. Open Developer Tools → Application → Cookies
3. Copy the full `Cookie` header and paste it into the integration
4. Copy the decoded `XSRF-TOKEN` value

### Additional settings

- **Order Detail ID** — your Mozillion order identifier (found in the URL when viewing your plan)
- **Data keys** — customize the JSON paths for `data_usage` and `data_remaining` if they differ from the defaults
