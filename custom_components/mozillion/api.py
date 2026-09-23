"""Client for the Mozillion website (login, dashboard and data usage).

The usage flow is asynchronous on Mozillion's side: ``/get-data-usage`` only
kicks off a refresh and answers ``{"status": "pending"}`` while the number is
being regenerated. The dashboard then polls ``/get-data-usage-status/<sim meta
id>`` until it returns ``{"status": "success", ...}`` with the actual figures,
so this client mirrors that handshake.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pyotp
from aiohttp import ClientError, ClientResponse, ClientSession
from yarl import URL

from .const import (
    BASE_URL,
    CHECK_BALANCE_PATH,
    DASHBOARD_PATH,
    DATA_USAGE_PATH,
    DATA_USAGE_STATUS_PATH,
    DEFAULT_ORIGIN,
    LOGIN_PATH,
    LOGIN_POST_PATH,
    TWO_FACTOR_PATH,
    USAGE_POLL_ATTEMPTS,
    USAGE_POLL_DELAY,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)

# Statuses Mozillion uses once the usage figures are ready.
_USAGE_DONE_STATUSES = ("success", "completed")


class MozillionAuthError(RuntimeError):
    """Raised when the session is no longer authenticated (cookie/token expired)."""


@dataclass(frozen=True, slots=True)
class MozillionSim:
    """A SIM as advertised by the Mozillion dashboard.

    Field names mirror the ``data-*`` attributes on the dashboard's
    ``.sim-option`` buttons so the mapping stays auditable against the page.
    """

    sim_meta_id: str
    order_detail_id: str
    sim_number: str = ""
    iccid: str = ""
    label: str = ""
    status: str = ""
    reset_label: str = ""
    days_left: str = ""
    used_data: float | None = None
    total_data: float | None = None
    used_data_gbr: float | None = None
    total_data_gbr: float | None = None
    used_data_global: float | None = None
    total_data_global: float | None = None
    is_unlimited: bool = False
    plan_duration: str = ""
    plan_data_tariff: str = ""
    plan_roaming: str = ""
    plan_texts_minutes: str = ""
    plan_is_data_only: bool = False

    @property
    def display_name(self) -> str:
        """Return the label used for the config entry title and device name."""

        if self.sim_number and self.plan_data_tariff:
            return f"{self.sim_number} ({self.plan_data_tariff})"
        return self.sim_number or self.label or f"SIM {self.sim_meta_id}"


class MozillionClient:
    """Thin wrapper around the Mozillion website endpoints."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def async_login(
        self,
        email: str,
        password: str,
        totp_secret: str | None = None,
        origin: str = DEFAULT_ORIGIN,
    ) -> tuple[str, str | None]:
        """Perform login (and 2FA if Mozillion asks for it).

        Returns ``(cookie_header, xsrf_header)``.
        """

        _LOGGER.debug("Starting login for email=%s, totp=%s", email, bool(totp_secret))

        token = await self._async_fetch_login_token(origin)
        totp = _build_totp(totp_secret) if totp_secret else None
        final_url, html = await self._async_post_form(
            f"{BASE_URL}{LOGIN_POST_PATH}",
            data={"_token": token, "email": email, "password": password},
            referer=f"{BASE_URL}{LOGIN_PATH}",
            origin=origin,
        )

        if "/2fa" in final_url:
            if totp is None:
                raise RuntimeError(
                    "Mozillion asked for a 2FA code but no TOTP secret is "
                    "configured. Add the Base32 secret to the integration."
                )
            await self._async_submit_two_factor(
                token=_extract_csrf(html) or token,
                totp=totp,
                referer=final_url,
                origin=origin,
            )
        elif final_url.rstrip("/").endswith(LOGIN_PATH):
            raise MozillionAuthError(
                "Mozillion rejected the email/password combination"
            )

        cookie_header, xsrf = _build_cookie_header(self._session)
        if not cookie_header:
            raise RuntimeError("No cookies returned after login")

        _LOGGER.debug(
            "Login complete, cookie_header length=%s, xsrf=%s",
            len(cookie_header),
            bool(xsrf),
        )
        return cookie_header, xsrf

    async def _async_fetch_login_token(self, origin: str) -> str:
        """Fetch the login page and return its CSRF token."""

        url = f"{BASE_URL}{LOGIN_PATH}"
        try:
            async with self._session.get(
                url, headers={"Origin": origin, "User-Agent": USER_AGENT}
            ) as resp:
                resp.raise_for_status()
                html = await resp.text()
        except ClientError as err:
            raise RuntimeError(f"Error fetching login page: {err}") from err

        token = _extract_csrf(html)
        if not token:
            raise RuntimeError("Could not find CSRF token on login page")
        return token

    async def _async_submit_two_factor(
        self, token: str, totp: pyotp.TOTP, referer: str, origin: str
    ) -> None:
        """Submit a TOTP code and verify the session actually advanced."""

        url = f"{BASE_URL}{TWO_FACTOR_PATH}"
        code = totp.now()
        _LOGGER.debug("Submitting 2FA code")
        final_url, _ = await self._async_post_form(
            url,
            data={"_token": token, "code": code},
            referer=referer,
            origin=origin,
        )
        if "/2fa" in final_url:
            raise MozillionAuthError(
                "2FA verification failed - Mozillion rejected the TOTP code"
            )

    async def _async_post_form(
        self, url: str, data: dict[str, str], referer: str, origin: str
    ) -> tuple[str, str]:
        """POST a form, following redirects, and return (final_url, body)."""

        try:
            async with self._session.post(
                url,
                data=data,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml,application/xml;q=0.9,"
                        "*/*;q=0.8"
                    ),
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": origin,
                    "Referer": referer,
                    "User-Agent": USER_AGENT,
                },
            ) as resp:
                resp.raise_for_status()
                return str(resp.url), await resp.text()
        except ClientError as err:
            raise RuntimeError(f"Request to {url} failed: {err}") from err

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------

    async def async_fetch_sims(
        self,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> list[MozillionSim]:
        """Fetch the dashboard and return every SIM it advertises."""

        html = await self._async_get_text(
            f"{BASE_URL}{DASHBOARD_PATH}",
            headers=self._page_headers(cookie_header, xsrf_token),
        )

        sims = parse_sims(html)
        if sims:
            _LOGGER.debug("Extracted %d SIM(s) from the dashboard", len(sims))
            return sims

        # The markup is our only source of SIM ids, so distinguish "the site
        # changed" from "we got bounced to the login page".
        if _looks_like_login_page(html):
            raise MozillionAuthError(
                "Mozillion returned the login page instead of the dashboard; "
                "the session has expired"
            )
        raise RuntimeError(
            "No SIMs found on the Mozillion dashboard. The page markup may have "
            "changed, or the account has no active SIMs."
        )

    async def async_fetch_sim(
        self,
        sim_meta_id: str,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> MozillionSim:
        """Fetch the dashboard and return the one SIM with this meta id.

        The dashboard carries the plan, reset date and service status that the
        JSON usage endpoints do not expose.
        """

        sims = await self.async_fetch_sims(
            cookie_header=cookie_header, xsrf_token=xsrf_token
        )
        for sim in sims:
            if sim.sim_meta_id == sim_meta_id:
                return sim

        raise RuntimeError(
            f"SIM {sim_meta_id} is no longer listed on the Mozillion dashboard; "
            "it may have been removed from the account"
        )

    # ------------------------------------------------------------------
    # Out-of-bundle wallet
    # ------------------------------------------------------------------

    async def async_fetch_overspend(
        self,
        order_detail_id: str,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> dict[str, Any]:
        """Read the out-of-bundle wallet position for an order.

        This is the call the dashboard's own wallet refresh button makes. It
        answers ``{"success": true, "balance": …, "spent": …, "remaining": …,
        "reached": …}``.
        """

        payload = await self._async_get_json(
            f"{BASE_URL}{CHECK_BALANCE_PATH}",
            headers=self._api_headers(cookie_header, xsrf_token),
            params={"order_detail_id": order_detail_id},
        )

        if not payload.get("success"):
            raise RuntimeError(
                str(payload.get("message") or "Mozillion rejected the balance check")
            )
        return payload

    # ------------------------------------------------------------------
    # Data usage
    # ------------------------------------------------------------------

    async def async_get_usage(
        self,
        order_detail_id: str,
        sim_meta_id: str,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> dict[str, Any]:
        """Trigger a usage refresh and return the completed payload."""

        headers = self._api_headers(cookie_header, xsrf_token)
        _LOGGER.debug(
            "Triggering usage update for order_detail_id=%s sim_meta_id=%s",
            order_detail_id,
            sim_meta_id,
        )

        payload = await self._async_get_json(
            f"{BASE_URL}{DATA_USAGE_PATH}",
            headers=headers,
            params={
                "order_detail_id": order_detail_id,
                "sim_meta_id": sim_meta_id,
            },
        )

        if _is_usage_ready(payload):
            return payload
        _raise_for_usage_error(payload)

        # Still pending: poll the status endpoint until the figures are ready.
        status_url = f"{BASE_URL}{DATA_USAGE_STATUS_PATH}/{sim_meta_id}"
        for attempt in range(USAGE_POLL_ATTEMPTS):
            if attempt:
                await asyncio.sleep(USAGE_POLL_DELAY)
            payload = await self._async_get_json(status_url, headers=headers)
            if _is_usage_ready(payload):
                _LOGGER.debug("Usage ready after %d status poll(s)", attempt + 1)
                return payload
            _raise_for_usage_error(payload)

        raise RuntimeError(
            "Mozillion did not finish preparing the usage data in time "
            f"({USAGE_POLL_ATTEMPTS} attempts)"
        )

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    def _page_headers(
        self, cookie_header: str, xsrf_token: str | None = None
    ) -> dict[str, str]:
        """Headers for a normal HTML page navigation."""

        headers = {
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,*/*;q=0.8"
            ),
            "DNT": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": USER_AGENT,
            "Cookie": cookie_header,
        }
        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token
        return headers

    def _api_headers(
        self, cookie_header: str, xsrf_token: str | None = None
    ) -> dict[str, str]:
        """Headers for the dashboard's own XHR calls."""

        headers = {
            "Accept": "application/json",
            "DNT": "1",
            "Referer": f"{BASE_URL}{DASHBOARD_PATH}",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie_header,
        }
        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token
        return headers

    async def _async_get_text(self, url: str, headers: dict[str, str]) -> str:
        """GET a URL and return the decoded body."""

        try:
            async with self._session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                return await resp.text()
        except ClientError as err:
            raise RuntimeError(f"Error fetching {url}: {err}") from err

    async def _async_get_json(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """GET a JSON endpoint, translating session expiry into an auth error."""

        try:
            async with self._session.get(url, headers=headers, params=params) as resp:
                await _require_authenticated(resp)
                resp.raise_for_status()
                try:
                    data = await resp.json(content_type=None)
                except ValueError as err:
                    raise RuntimeError(
                        f"Mozillion returned a non-JSON response from {url}"
                    ) from err
        except ClientError as err:
            raise RuntimeError(f"Error communicating with Mozillion: {err}") from err

        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected response from {url}: {data!r}")
        return data


# ----------------------------------------------------------------------
# Dashboard parsing
# ----------------------------------------------------------------------

# The dashboard renders one <button class="sim-option" data-sim-id="…"> per SIM.
# Attribute values never contain ">", so matching the opening tag is enough, and
# the class list is split on whitespace so the decorative "sim-option-check"
# element is never mistaken for a SIM button.
_SIM_BUTTON_RE = re.compile(r"<button\b[^>]*>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r'\bclass="([^"]*)"', re.IGNORECASE)
_DATA_ATTR_RE = re.compile(r'(data-[a-z0-9-]+)\s*=\s*"([^"]*)"', re.IGNORECASE)


def parse_sims(html: str) -> list[MozillionSim]:
    """Extract the SIMs advertised by the dashboard HTML."""

    sims: list[MozillionSim] = []
    for match in _SIM_BUTTON_RE.finditer(html):
        tag = match.group(0)

        class_match = _CLASS_ATTR_RE.search(tag)
        if class_match is None or "sim-option" not in class_match.group(1).split():
            continue

        attrs = {name.lower(): value for name, value in _DATA_ATTR_RE.findall(tag)}
        sim_meta_id = attrs.get("data-sim-id", "").strip()
        order_detail_id = attrs.get("data-orderdetail-id", "").strip()
        if not sim_meta_id or not order_detail_id:
            _LOGGER.debug("Skipping sim-option without ids: %s", attrs)
            continue

        sims.append(
            MozillionSim(
                sim_meta_id=sim_meta_id,
                order_detail_id=order_detail_id,
                sim_number=attrs.get("data-sim-number", "").strip(),
                iccid=attrs.get("data-iccid", "").strip(),
                label=attrs.get("data-label", "").strip(),
                status=attrs.get("data-status", "").strip(),
                reset_label=attrs.get("data-usage-reset-label", "").strip(),
                days_left=attrs.get("data-days-left", "").strip(),
                used_data=_to_float(attrs.get("data-used-data")),
                total_data=_to_float(attrs.get("data-total-data")),
                used_data_gbr=_to_float(attrs.get("data-used-data-gbr")),
                total_data_gbr=_to_float(attrs.get("data-total-data-gbr")),
                used_data_global=_to_float(attrs.get("data-used-data-global")),
                total_data_global=_to_float(attrs.get("data-total-data-global")),
                is_unlimited=_to_bool(attrs.get("data-is-unlimited")),
                plan_duration=attrs.get("data-plan-duration", "").strip(),
                plan_data_tariff=attrs.get("data-plan-data-tariff", "").strip(),
                plan_roaming=attrs.get("data-plan-roaming", "").strip(),
                plan_texts_minutes=attrs.get("data-plan-texts-minutes", "").strip(),
                plan_is_data_only=_to_bool(attrs.get("data-plan-is-data-only")),
            )
        )
    return sims


def _looks_like_login_page(html: str) -> bool:
    """Return True when the HTML is Mozillion's login page."""

    return 'name="password"' in html or LOGIN_POST_PATH in html


# Mozillion renders the reset label in English, e.g. "19 Oct" or "19 Oct 2026".
_RESET_LABEL_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]+)(?:\s+(\d{4}))?$")
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def parse_reset_date(
    label: str | None, days_left: str | None = None, today: date | None = None
) -> date | None:
    """Work out the next data reset date from the dashboard's markup.

    The dashboard gives either a day count (``data-days-left``) or a day/month
    label such as ``"19 Oct"``. A day count is exact, so it wins. The label has
    no year, so the next occurrence is assumed -- a reset date is always in the
    future by definition. Both of those inferences are ours, not Mozillion's;
    the raw label is exposed alongside so the original is always visible.

    The month names are matched against an English table rather than
    ``strptime``: the markup is server-rendered in English, and strptime warns
    that a year-less parse is ambiguous (Python 3.14 deprecation).
    """

    today = today or date.today()

    if days_left:
        try:
            return today + timedelta(days=int(days_left.strip()))
        except ValueError:
            _LOGGER.debug("Ignoring non-numeric data-days-left %r", days_left)

    if not label:
        return None

    match = _RESET_LABEL_RE.match(label.strip())
    if match is None:
        _LOGGER.debug("Could not interpret the reset label %r", label)
        return None

    day_text, month_text, year_text = match.groups()
    month = _MONTHS.get(month_text[:3].lower())
    if month is None:
        _LOGGER.debug("Unknown month %r in the reset label", month_text)
        return None

    year = int(year_text) if year_text else today.year
    try:
        candidate = date(year, month, int(day_text))
    except ValueError:
        _LOGGER.debug("Invalid day in the reset label %r", label)
        return None

    if year_text or candidate >= today:
        return candidate

    # The reset for this year has been and gone, so it must be the next one.
    try:
        return candidate.replace(year=candidate.year + 1)
    except ValueError:
        # 29 February: the following year has no such day.
        _LOGGER.debug("Cannot move %s into the next year", candidate)
        return None


def _to_float(value: str | None) -> float | None:
    """Parse a dashboard numeric attribute, treating blanks as unknown."""

    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        _LOGGER.debug("Ignoring non-numeric dashboard value %r", value)
        return None


def _to_bool(value: str | None) -> bool:
    """Parse a dashboard boolean attribute ("1"/"true")."""

    return str(value).strip().lower() in ("1", "true", "yes", "on")


# ----------------------------------------------------------------------
# Usage payload helpers
# ----------------------------------------------------------------------


def _is_usage_ready(payload: dict[str, Any]) -> bool:
    """Return True when the payload holds the final usage figures."""

    return str(payload.get("status", "")).lower() in _USAGE_DONE_STATUSES


def _raise_for_usage_error(payload: dict[str, Any]) -> None:
    """Raise when Mozillion reported a failure for the usage refresh."""

    if str(payload.get("status", "")).lower() == "error":
        raise RuntimeError(
            payload.get("message") or "Mozillion failed to refresh the usage data"
        )


async def _require_authenticated(resp: ClientResponse) -> None:
    """Raise MozillionAuthError if the response indicates an expired session.

    Mozillion does not return a tidy 401 for an expired session; instead the
    request is bounced to the HTML login page (HTTP 200, ``text/html``). We
    detect both the explicit auth status codes and the "got HTML instead of
    JSON" case so the coordinator can transparently re-authenticate.
    """

    if isinstance(resp.status, int) and resp.status in (401, 403):
        raise MozillionAuthError(
            f"Mozillion rejected the request (HTTP {resp.status}); "
            "the session has likely expired"
        )
    content_type = str(resp.headers.get("Content-Type", ""))
    if "text/html" in content_type and "application/json" not in content_type:
        raise MozillionAuthError(
            "Mozillion returned an HTML login page instead of JSON; "
            "the session has likely expired"
        )


def _build_totp(secret: str) -> pyotp.TOTP:
    """Build a TOTP generator from whatever form of secret was supplied.

    Mozillion hands out a Base32 secret, but people usually have the
    ``otpauth://`` link (from the QR code) or a space/hyphen separated secret
    to hand, so accept all of them.
    """

    normalised = _normalise_totp_secret(secret)
    try:
        base64.b32decode(normalised, casefold=True)
    except (binascii.Error, ValueError) as err:
        raise RuntimeError(
            "The TOTP secret is not valid Base32. Paste the secret shown by "
            "Mozillion, or the whole otpauth:// link it gives you."
        ) from err
    return pyotp.TOTP(normalised)


def _normalise_totp_secret(secret: str) -> str:
    """Reduce a secret or provisioning URI to a padded Base32 string."""

    value = secret.strip().strip("\"'").strip()

    if value.lower().startswith("otpauth://"):
        secret_params = parse_qs(urlparse(value).query).get("secret") or []
        value = secret_params[0] if secret_params else ""

    value = re.sub(r"[\s-]", "", value).upper()
    if not value:
        raise RuntimeError(
            "No TOTP secret found. Provide the Base32 secret or the otpauth:// link."
        )

    # pyotp feeds the secret straight to base64.b32decode, which rejects
    # unpadded input, so pad it here for both validation and generation.
    missing_padding = len(value) % 8
    if missing_padding:
        value += "=" * (8 - missing_padding)
    return value


def _extract_csrf(text: str) -> str | None:
    """Extract a CSRF token from HTML."""

    patterns = [
        r'name="_token"\s+value="([^"]+)"',
        r'meta name="csrf-token" content="([^"]+)"',
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(1)
    return None


def _build_cookie_header(session: ClientSession) -> tuple[str, str | None]:
    """Build cookie header and decode XSRF token.

    The cookie jar is filtered to Mozillion's own URL because Home Assistant
    shares one ``aiohttp`` session across every integration, so the raw jar can
    contain unrelated cookies.
    """

    cookies = session.cookie_jar.filter_cookies(URL(BASE_URL))
    if not cookies:
        return "", None

    xsrf = None
    raw_xsrf = cookies.get("XSRF-TOKEN")
    if raw_xsrf is not None:
        xsrf = unquote(raw_xsrf.value)

    cookie_header = "; ".join(
        f"{key}={morsel.value}" for key, morsel in cookies.items()
    )
    return cookie_header, xsrf
