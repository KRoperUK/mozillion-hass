# Troubleshooting

## Common issues

### "Failed to authenticate"

- **Cookies expired** — Mozillion session cookies expire after a period of inactivity. Re-authenticate by:
  - Auto-login: the integration will automatically re-login on the next poll cycle
  - Manual cookies: get fresh cookies from your browser and update the config entry
- **Wrong credentials** — double-check your email and password

### "Invalid order_detail_id"

Your order detail ID is the numeric ID in the Mozillion URL when viewing your plan details. It looks like: `https://my.mozillion.com/orders/12345/details`

### Sensors show "unavailable"

- The Mozillion API may be temporarily down. Sensors will update on the next poll cycle.
- Check Home Assistant logs: enable debug logging for `custom_components.mozillion` in `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.mozillion: debug
```

### TOTP issues

- The TOTP secret must be in **Base32** format, not hex.
- If you use Google Authenticator, the setup key shown during enrollment is the Base32 secret.
