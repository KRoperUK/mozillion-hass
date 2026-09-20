# Troubleshooting

Enable debug logging first — most answers are in there:

```yaml
logger:
  default: warning
  logs:
    custom_components.mozillion: debug
```

## Setup problems

### "Cannot connect" while adding the integration

- **Wrong email or password** — Mozillion bounces the login back to its login
  page, which the integration reports as rejected credentials.
- **2FA is on but no TOTP secret was given** — Mozillion asks for a code and the
  integration cannot answer. Add the secret or the `otpauth://` link.
- **TOTP secret rejected** — the field takes a Base32 secret or a full
  `otpauth://totp/…?secret=…` link. A hex secret, or the six-digit code itself,
  will not work.
- **The SIM list could not be read** — the integration falls back to asking for
  the order detail ID and SIM meta ID by hand. You can find both in the page
  source of the Mozillion dashboard: they are the `data-orderdetail-id` and
  `data-sim-id` attributes on the SIM buttons.

### "This SIM is already configured"

Each SIM can only be tracked once. Delete the existing entry, or use
**Reconfigure** on it instead of adding a second entry.

## After setup

### Sensors show "unavailable"

The last poll failed, so Home Assistant marks the entities unavailable while
keeping the last known values in the recorder.

- Check the logs for `Update failed:` or `Error communicating with Mozillion`.
- A `429` means Mozillion rate-limited the request; increase the scan interval.
- Transient outages clear themselves on the next poll cycle.

### Sensors show "unknown"

The request succeeded but the figures did not parse. Check the `raw` attribute on
any sensor — it contains the exact payload the integration received. If Mozillion
has changed its field names, the `raw` attribute is what to include in a bug
report.

### Usage never updates

Mozillion regenerates usage server-side and answers `pending` until it is ready;
the integration polls for up to about 15 seconds per cycle. If it never becomes
ready, the cycle fails and retries at the next scan interval. Mozillion's own
dashboard shows the same delay on its refresh button.

### "Authentication failed" / re-authentication requested

- **Email/password entries** refresh transparently. A prompt means Mozillion
  rejected the stored credentials — usually after a password change. Enter the
  new password.
- **Cookie-only entries** cannot renew themselves. When the session expires you
  are asked for a fresh cookie, or you can add email/password to enable
  automatic renewal.

### The numbers disagree with the Mozillion website

The website shows the same `usedData` / `totalData` pair the sensors use. If the
website itself was refreshed more recently than the last poll, force a refresh by
reloading the integration or wait for the next scan interval.

The integration does not apply its own rounding: `Remaining` is `Total − Usage`
floored at zero, and `Usage Percentage` is `Usage ÷ Total` capped at 100, which
is exactly what the Mozillion dashboard does.

### Partial or surprising bucket figures

Mozillion returns three pairs — `usedData`/`totalData`,
`usedDataGbr`/`totalDataGbr` and `usedDataGlobal`/`totalDataGlobal`. The
headline pair drives the sensors. The other two are exposed as attributes on
each sensor but their exact meaning is unverified; see the "Known limitations"
section of the [documentation](index.md#known-limitations).

## Reporting a problem

Include:

1. The integration version (*Settings → Devices & Services → Mozillion*).
2. The diagnostics download (⋮ → *Download diagnostics*) — credentials, cookies
   and identifiers are redacted automatically.
3. Debug logs covering a full poll cycle.
