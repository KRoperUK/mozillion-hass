# Security Policy

## Supported versions

This is a Home Assistant custom integration distributed via HACS. Security fixes
are made against the latest release only. Please update to the newest version
before reporting a suspected vulnerability.

## What this integration handles

The integration stores and uses sensitive credentials on your behalf:

- Your Mozillion **session cookies** and **XSRF token**, or your Mozillion
  **email/password** and optional **TOTP secret**.
- Session tokens are held in Home Assistant's config-entry storage and are
  redacted from diagnostics downloads. They are never logged (debug logging
  deliberately avoids printing secrets, tokens, or authorization codes).

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Instead, report privately via GitHub's
[private vulnerability reporting](https://github.com/KRoperUK/mozillion-hass/security/advisories/new)
("**Report a vulnerability**" on the repository's **Security** tab). This opens a
private advisory visible only to the maintainers.

Please include:

- A description of the issue and its impact.
- Steps to reproduce (or a proof of concept).
- The integration version and Home Assistant version.

You can expect an initial acknowledgement within a few days. Once a fix is
available it will be released and, where appropriate, a security advisory will be
published crediting the reporter (unless you prefer to remain anonymous).

## Scope

This policy covers the integration code in this repository. Vulnerabilities in
Home Assistant core or the Mozillion service should be reported to their
respective projects.
