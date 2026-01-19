"""Client for Mozillion login and data usage endpoints."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Tuple
from urllib.parse import unquote

import pyotp
from aiohttp import ClientError, ClientSession

from .const import BASE_URL, DEFAULT_BASE_URL, STATUS_BASE_URL, DEFAULT_ORIGIN

_LOGGER = logging.getLogger(__name__)


class MozillionClient:
    """Thin wrapper around Mozillion endpoints."""

    def __init__(self, session: ClientSession, base_url: str = DEFAULT_BASE_URL) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")

    async def async_login(
        self,
        email: str,
        password: str,
        totp_secret: str | None = None,
        origin: str = DEFAULT_ORIGIN,
    ) -> Tuple[str, str | None]:
        """Perform login (and 2FA if secret provided). Returns (cookie_header, xsrf_header)."""

        _LOGGER.debug("Starting login for email=%s, totp=%s", email, bool(totp_secret))

        # Step 1: fetch login page to get initial cookies and CSRF token
        login_url = f"{BASE_URL}/login"
        token = None
        try:
            async with self._session.get(login_url, headers={"Origin": origin}) as resp:
                resp.raise_for_status()
                text = await resp.text()
                token = _extract_csrf(text)
                _LOGGER.debug("Fetched login page, csrf token found: %s", bool(token))
        except ClientError as err:
            _LOGGER.error("Error fetching login page: %s", err)
            raise RuntimeError(f"Error fetching login page: {err}") from err

        if not token:
            _LOGGER.error("Could not find CSRF token on login page")
            raise RuntimeError("Could not find CSRF token on login page")

        # Step 2: submit credentials
        login_post = f"{BASE_URL}/login-post"
        form = {"_token": token, "email": email, "password": password}
        try:
            async with self._session.post(
                login_post,
                data=form,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": origin,
                    "Referer": login_url,
                },
            ) as resp:
                resp.raise_for_status()
                text = await resp.text()
                # If 2FA page returned, parse a fresh token from it
                token = _extract_csrf(text) or token
                _LOGGER.debug("Login POST completed, status=%s", resp.status)
        except ClientError as err:
            _LOGGER.error("Login request failed: %s", err)
            raise RuntimeError(f"Login request failed: {err}") from err

        # Step 3: handle TOTP if provided
        if totp_secret:
            twofa_url = f"{BASE_URL}/2fa/verify"
            code = pyotp.TOTP(totp_secret).now()
            _LOGGER.debug("Submitting 2FA code: %s", code)
            form_2fa = {"_token": token, "code": code}
            try:
                async with self._session.post(
                    twofa_url,
                    data=form_2fa,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": origin,
                        "Referer": twofa_url,
                    },
                ) as resp:
                    resp.raise_for_status()
                    # Read response to ensure cookies are set
                    final_html = await resp.text()
                    _LOGGER.debug("2FA verification complete, status=%s, final_url=%s", resp.status, str(resp.url))
                    
                    # Verify we're actually logged in by checking the final URL
                    if "/2fa" in str(resp.url):
                        raise RuntimeError("2FA verification failed - still on 2FA page")
                        
            except ClientError as err:
                _LOGGER.error("2FA verification failed: %s", err)
                raise RuntimeError(f"2FA verification failed: {err}") from err

        cookie_header, xsrf = _build_cookie_header(self._session)
        if not cookie_header:
            _LOGGER.error("No cookies returned after login")
            raise RuntimeError("No cookies returned after login")

        _LOGGER.debug("Login complete, cookie_header length=%s, xsrf=%s", len(cookie_header), bool(xsrf))
        return cookie_header, xsrf

    async def async_fetch_dashboard_ids(
        self,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> list[dict[str, str]]:
        """Fetch user dashboard and extract SIM plan IDs and order detail IDs."""

        _LOGGER.debug("Fetching dashboard to extract IDs")

        headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "DNT": "1",
            "Priority": "u=1, i",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-GPC": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
            "Cookie": cookie_header,
        }

        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token

        url = f"{BASE_URL}/user-dashboard"
        try:
            async with self._session.get(url, headers=headers) as resp:
                resp.raise_for_status()
                html = await resp.text()
                _LOGGER.debug(
                    "Dashboard HTML fetch success, status=%s, html length=%s",
                    resp.status,
                    len(html),
                )

                # Parse HTML to extract SIM options from <select id="simlist">
                plans = []
                import re
                
                # Find the simlist select block
                simlist_pattern = r'<select[^>]*id="simlist"[^>]*>(.*?)</select>'
                simlist_match = re.search(simlist_pattern, html, re.DOTALL | re.IGNORECASE)
                
                if simlist_match:
                    simlist_content = simlist_match.group(1)
                    _LOGGER.debug("Found simlist select block, length=%d", len(simlist_content))
                    
                    # Extract each option tag
                    option_pattern = r'<option[^>]*>(.*?)</option>'
                    option_matches = re.finditer(option_pattern, simlist_content, re.DOTALL | re.IGNORECASE)
                    
                    for option_match in option_matches:
                        option_tag = option_match.group(0)
                        
                        # Extract attributes from the option tag
                        value_match = re.search(r'value\s*=\s*["\']([^"\']+)["\']', option_tag, re.IGNORECASE)
                        sim_number_match = re.search(r'data-sim-number\s*=\s*["\']([^"\']+)["\']', option_tag, re.IGNORECASE)
                        order_detail_match = re.search(r'data-orderdetail_id\s*=\s*["\']([^"\']+)["\']', option_tag, re.IGNORECASE)
                        
                        if value_match and order_detail_match:
                            sim_plan_id = value_match.group(1).strip()
                            order_detail_id = order_detail_match.group(1).strip()
                            sim_number = sim_number_match.group(1).strip() if sim_number_match else ""
                            
                            if sim_plan_id and order_detail_id:
                                name = sim_number if sim_number else f"SIM Plan {sim_plan_id}"
                                plans.append({
                                    "sim_plan_id": str(sim_plan_id),
                                    "order_detail_id": str(order_detail_id),
                                    "name": str(name),
                                    "sim_number": str(sim_number) if sim_number else "",
                                })
                                _LOGGER.debug(
                                    "Found plan: name=%s, sim_id=%s, order_detail_id=%s",
                                    name,
                                    sim_plan_id,
                                    order_detail_id,
                                )
                else:
                    _LOGGER.warning("Could not find simlist select element in HTML")

                _LOGGER.info("Extracted %d plan(s) from dashboard", len(plans))
                return plans
        except ClientError as err:
            _LOGGER.error("Error fetching dashboard: %s", err)
            raise RuntimeError(f"Error fetching dashboard: {err}") from err
        except Exception as err:
            _LOGGER.error("Error parsing dashboard HTML: %s", err)
            raise RuntimeError(f"Error parsing dashboard: {err}") from err

    async def async_get_usage(
        self,
        order_detail_id: str,
        sim_plan_id: str,
        cookie_header: str,
        xsrf_token: str | None = None,
    ) -> Dict[str, Any]:
        """Fetch usage data from Mozillion."""

        _LOGGER.debug("Triggering usage update for order_detail_id=%s", order_detail_id)

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "DNT": "1",
            "Priority": "u=1, i",
            "Referer": "https://www.mozillion.com/user-dashboard",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Sec-GPC": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/143.0.0.0 Safari/537.36"
            ),
            "X-Requested-With": "XMLHttpRequest",
            "Cookie": cookie_header,
        }

        if xsrf_token:
            headers["X-XSRF-TOKEN"] = xsrf_token

        # Step 1: Trigger the data usage update
        trigger_url = f"{self._base_url}"
        try:
            async with self._session.get(
                trigger_url, params={"order_detail_id": order_detail_id}, headers=headers
            ) as resp:
                resp.raise_for_status()
                trigger_data = await resp.json()
                _LOGGER.debug("Trigger success, status=%s, response=%s", resp.status, trigger_data)
        except ClientError as err:
            _LOGGER.error("Error triggering Mozillion update: %s", err)
            raise RuntimeError(f"Error triggering Mozillion update: {err}") from err
        except ValueError as err:
            _LOGGER.error("Mozillion trigger response is not JSON: %s", err)
            raise RuntimeError("Mozillion trigger response is not JSON") from err

        # Step 2: Fetch the actual usage status
        status_url = f"{STATUS_BASE_URL}/{sim_plan_id}"
        _LOGGER.debug("Fetching usage status from %s", status_url)
        try:
            async with self._session.get(status_url, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                _LOGGER.debug("Usage fetch success, status=%s, data keys=%s", resp.status, list(data.keys()) if isinstance(data, dict) else type(data))
                return data
        except ClientError as err:
            _LOGGER.error("Error communicating with Mozillion: %s", err)
            raise RuntimeError(f"Error communicating with Mozillion: {err}") from err
        except ValueError as err:
            _LOGGER.error("Mozillion response is not JSON: %s", err)
            raise RuntimeError("Mozillion response is not JSON") from err


def _extract_csrf(text: str) -> str | None:
    """Extract a CSRF token from HTML."""

    patterns = [r'name="_token"\s+value="([^"]+)"', r'meta name="csrf-token" content="([^"]+)"']
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return match.group(1)
    return None


def _build_cookie_header(session: ClientSession) -> Tuple[str, str | None]:
    """Build cookie header and decode XSRF token."""

    jar = session.cookie_jar
    cookies = []
    xsrf = None
    for cookie in jar:
        cookies.append(f"{cookie.key}={cookie.value}")
        if cookie.key == "XSRF-TOKEN":
            xsrf = unquote(cookie.value)
    cookie_header = "; ".join(cookies)
    return cookie_header, xsrf
