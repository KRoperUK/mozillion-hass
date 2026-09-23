"""End-to-end smoke check against the real Mozillion API.

This is deliberately NOT a pytest module: the Home Assistant test plugin
(``pytest_homeassistant_custom_component``) disables DNS resolution for every
pytest test, so a live HTTP check cannot run from inside the suite. Run it
directly instead:

    uv run python scripts/live_check.py

Credentials come from ``.env`` (or the environment):

    MOZILLION_EMAIL=you@example.com
    MOZILLION_PASSWORD=your-password
    MOZILLION_2FA=BASE32SECRET      # optional; a whole otpauth:// link works too
    MOZILLION_ORIGIN=https://www.mozillion.com   # optional

It exercises the same path the integration uses — login (including 2FA), read
the SIM list from the dashboard, trigger a usage refresh and poll it to
completion — then prints what it parsed. Exits non-zero on failure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from custom_components.mozillion.api import (  # noqa: E402
    MozillionClient,
    MozillionSim,
    parse_reset_date,
)
from custom_components.mozillion.const import DEFAULT_ORIGIN  # noqa: E402
from custom_components.mozillion.coordinator import (  # noqa: E402
    _build_coordinator_data,
)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
_LOGGER = logging.getLogger("live_check")


class CheckFailed(RuntimeError):
    """A live assertion failed."""


def _require(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is not set. Add it to .env or export it, then retry.")
    return value


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)
    print(f"  ok: {message}")


async def _run() -> int:
    from aiohttp import ClientSession, TCPConnector
    from aiohttp.resolver import ThreadedResolver

    email = _require("MOZILLION_EMAIL")
    password = _require("MOZILLION_PASSWORD")
    totp = os.environ.get("MOZILLION_2FA") or os.environ.get("MOZILLION_TOTP_SECRET")
    origin = os.environ.get("MOZILLION_ORIGIN", DEFAULT_ORIGIN)

    connector = TCPConnector(resolver=ThreadedResolver())
    async with ClientSession(connector=connector) as session:
        client = MozillionClient(session)

        print("1. login")
        cookie, xsrf = await client.async_login(
            email=email, password=password, totp_secret=totp, origin=origin
        )
        _check(bool(cookie), "login returned a session cookie")

        print("2. read the dashboard SIM list")
        sims = await client.async_fetch_sims(cookie_header=cookie, xsrf_token=xsrf)
        _check(bool(sims), f"dashboard lists {len(sims)} SIM(s)")
        for sim in sims:
            _check(bool(sim.sim_meta_id), f"{_safe_name(sim)} has a sim_meta_id")
            _check(
                bool(sim.order_detail_id),
                f"{_safe_name(sim)} has an order_detail_id",
            )
            print(_describe(sim))

        sim = sims[0]
        print(f"3. usage for {_safe_name(sim)} (trigger + poll)")
        raw = await client.async_get_usage(
            order_detail_id=sim.order_detail_id,
            sim_meta_id=sim.sim_meta_id,
            cookie_header=cookie,
            xsrf_token=xsrf,
        )
        _check(
            raw.get("status") in ("success", "completed"),
            f"usage finished with status {raw.get('status')!r}",
        )

        data = _build_coordinator_data(raw)
        _check(data["usage"] is not None, f"usedData parsed as {data['usage']}")
        _check(data["total"] is not None, f"totalData parsed as {data['total']}")
        print(
            f"  remaining={data['remaining']} "
            f"percentage={data['usage_percentage']} "
            f"unlimited={data['unlimited']}"
        )

        print(f"4. dashboard detail for {_safe_name(sim)}")
        detail = await client.async_fetch_sim(
            sim_meta_id=sim.sim_meta_id, cookie_header=cookie, xsrf_token=xsrf
        )
        _check(
            detail.sim_meta_id == sim.sim_meta_id,
            f"read back the same SIM (status={detail.status!r})",
        )
        reset = parse_reset_date(detail.reset_label, detail.days_left)
        _check(
            reset is not None or not detail.reset_label,
            f"reset label {detail.reset_label!r} parsed as {reset}",
        )
        print(
            f"  plan={detail.plan_data_tariff!r} duration={detail.plan_duration!r} "
            f"roaming={detail.plan_roaming!r} texts={detail.plan_texts_minutes!r}"
        )
        print(
            f"  voicemail={detail.plan_voicemail} "
            f"parental_control={detail.plan_parental_control}"
        )
        print(
            f"  port_status={detail.port_status!r} "
            f"label={detail.port_status_label!r} date={detail.port_date!r}"
        )
        if detail.port_status_description:
            print(f"    {detail.port_status_description}")
        # Checked, not printed: the billing fields are financial data, so the
        # figures stay out of the log. What matters here is that they parse.
        _check(
            detail.billing_amount is None or isinstance(detail.billing_amount, float),
            "billing_amount parsed as a number (not printed)",
        )
        _check(
            isinstance(detail.has_bill, bool),
            "has_bill parsed as a boolean (not printed)",
        )

        print("5. out-of-bundle wallet balance")
        wallet = await client.async_fetch_overspend(
            order_detail_id=sim.order_detail_id,
            cookie_header=cookie,
            xsrf_token=xsrf,
        )
        print(f"  {wallet}")
        has_balance = (wallet.get("remaining") or 0) > 0
        has_spend = (wallet.get("spent") or 0) > 0
        active = bool(has_balance or has_spend)
        print(
            f"  wallet {'in use' if active else 'not topped up'} "
            f"(entities report {'values' if active else 'unavailable'})"
        )

    print("\nAll live checks passed.")
    return 0


def _mask(value: str, keep: int = 3) -> str:
    """Redact the middle of an identifier.

    This script reads real account data, and its output is exactly what someone
    pastes into a bug report, so the SIM number and ICCID are masked rather than
    printed in full.
    """

    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep * 2)}{value[-keep:]}"


def _describe(sim: MozillionSim) -> str:
    return (
        f"  {_safe_name(sim)}: sim_meta_id={_mask(sim.sim_meta_id)} "
        f"order_detail_id={_mask(sim.order_detail_id)} status={sim.status} "
        f"reset={sim.reset_label!r} "
        f"used={sim.used_data}/{sim.total_data} "
        f"gbr={sim.used_data_gbr}/{sim.total_data_gbr} "
        f"global={sim.used_data_global}/{sim.total_data_global} "
        f"unlimited={sim.is_unlimited}"
    )


def _safe_name(sim: MozillionSim) -> str:
    """The SIM's display name with the phone number masked."""

    return sim.display_name.replace(sim.sim_number, _mask(sim.sim_number))


def main() -> int:
    """Run the live check, reporting failures plainly."""

    try:
        return asyncio.run(_run())
    except CheckFailed as err:
        print(f"\nFAILED: {err}")
        return 1
    except Exception as err:
        print(f"\nFAILED: {type(err).__name__}: {err}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
