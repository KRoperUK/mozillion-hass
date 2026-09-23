# Installation & Setup

> **Prerequisites:** A [Mozillion](https://www.mozillion.com/) account with an
> active mobile plan.

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

## Manual installation

1. Download the latest release from [GitHub Releases](https://github.com/KRoperUK/mozillion-hass/releases)
2. Copy the `custom_components/mozillion` folder to your Home Assistant's `custom_components` directory
3. Restart Home Assistant
4. Go to Settings → Devices & Services → Add Integration
5. Search for "Mozillion" and follow the configuration steps

## Authentication

You need either the first option (recommended) or the second.

### Option A — email and password

1. Enter your Mozillion **email** and **password**.
2. If two-factor authentication is enabled on your account, enter the **TOTP
   secret**. Either paste the Base32 secret itself, or paste the whole
   `otpauth://totp/…?secret=…` link that Mozillion's QR code encodes — both work.
   Spaces, dashes and lower-case letters are tolerated.

The integration logs in, answers the 2FA challenge, and refreshes the session on
its own afterwards.

### Option B — session cookie

1. Log into Mozillion in your browser.
2. Open Developer Tools → Application → Cookies → `https://www.mozillion.com`.
3. Copy the **Cookie** header value and paste it into the *Cookie header* field.
4. Optionally copy the `XSRF-TOKEN` cookie into the *XSRF token header* field.

A cookie cannot be renewed by the integration, so when it expires you will be
asked to re-authenticate. Prefer Option A if you want unattended operation.

## Installation parameters

| Field | Required | Notes |
| --- | --- | --- |
| Email | With Option A | Mozillion account email |
| Password | With Option A | Mozillion account password |
| TOTP secret | If 2FA is on | Base32 secret or a full `otpauth://` link |
| Origin header | No | Advanced. Overrides the `Origin` sent when logging in |
| Cookie header | With Option B | Advanced. Full browser cookie string |
| XSRF token header | No | Advanced. Decoded `XSRF-TOKEN` cookie value |
| Scan interval | No | Advanced. Seconds between polls, minimum 60 |

After the credentials are accepted you are shown a **SIM** dropdown listing the
SIMs on your account, each labelled with its phone number and plan tariff. Pick
the one to track.

If the SIM list cannot be read, you are asked for the ids manually:

| Field | Required | Where to find it |
| --- | --- | --- |
| Order detail ID | Yes | `data-orderdetail-id` on the SIM buttons in the dashboard's page source |
| SIM meta ID | Yes | `data-sim-id` on the same element |
| SIM number | No | Used only for naming the device |

### Adding another SIM

The entry represents your Mozillion **account**, not one SIM. To track a second SIM,
open *Settings → Devices & Services → Mozillion* and choose **Add SIM** on the entry —
there is no need to enter your credentials again. Each SIM gets its own device and set
of entities, and the scan interval applies to all of them.

To point an existing SIM at a different one, use **Reconfigure** on that SIM's subentry
rather than on the account.

## Configuration parameters

Options are reached through *Settings → Devices & Services → Mozillion →
Configure*.

| Option | Default | Notes |
| --- | --- | --- |
| Scan interval | 3600 seconds | How often Home Assistant polls. Each poll also asks Mozillion to regenerate the figures, so there is no benefit in polling faster than the provider updates |

Changing credentials is done with **Reconfigure** on the account entry; changing
which SIM a subentry tracks is done with **Reconfigure** on that SIM. Both keep the
entry, its entities and their history.

## Removal

1. Go to *Settings → Devices & Services*.
2. Find **Mozillion Data**.
3. Open the ⋮ menu on the entry and choose **Delete**.
4. Confirm. The config entry, its device and all of its entities are removed.
5. If you installed through HACS, optionally remove the repository from HACS and
   delete `custom_components/mozillion` to remove the code as well.
