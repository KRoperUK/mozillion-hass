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
            _check(bool(sim.sim_meta_id), f"{sim.display_name} has a sim_meta_id")
            _check(
                bool(sim.order_detail_id),
                f"{sim.display_name} has an order_detail_id",
            )
            print(_describe(sim))

        sim = sims[0]
        print(f"3. usage for {sim.display_name} (trigger + poll)")
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

    print("\nAll live checks passed.")
    return 0


def _describe(sim: MozillionSim) -> str:
    return (
        f"  {sim.display_name}: sim_meta_id={sim.sim_meta_id} "
        f"order_detail_id={sim.order_detail_id} status={sim.status} "
        f"reset={sim.reset_label!r} "
        f"used={sim.used_data}/{sim.total_data} "
        f"gbr={sim.used_data_gbr}/{sim.total_data_gbr} "
        f"global={sim.used_data_global}/{sim.total_data_global} "
        f"unlimited={sim.is_unlimited}"
    )


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
