# Mozillion for Home Assistant

A custom integration that tracks your [Mozillion](https://www.mozillion.com/)
mobile data usage in Home Assistant — how much you have used, how much is left,
and whether the plan is unlimited.

## Supported devices

Any active Mozillion SIM (UK) that appears on the Mozillion dashboard. One entry
covers the whole account and each SIM is added to it, so a multi-SIM account gets one
device per SIM while your credentials are stored once.

The integration is cloud-only: it reads the same dashboard and JSON endpoints
the Mozillion website itself uses, so it works anywhere Home Assistant can
reach `www.mozillion.com`.

## Supported functions

- **Data usage sensors** — used, total allowance, remaining and percentage.
- **Unlimited plan detection** — a binary sensor that reports whether the plan
  has no data cap.
- **Automatic re-authentication** — with email/password the session is
  refreshed transparently, including TOTP two-factor if you supply the secret.
- **Cookie-only mode** — if you would rather not store your password, paste a
  session cookie instead.
- **SIM selection** — the dashboard's SIM list is read during setup, so you pick
  a SIM from a dropdown.
- **Reconfigure and reauth flows** — switch SIM or refresh credentials without
  deleting the entry.
- **Diagnostics** — downloadable from the integration page with credentials,
  cookies and identifiers redacted.

## Entities

| Entity | Type | Unit | Meaning |
| --- | --- | --- | --- |
| Usage | sensor | GB | Data used in the current period |
| Total | sensor | GB | Data allowance for the period |
| Remaining | sensor | GB | `Total − Usage`, never negative |
| Usage Percentage | sensor | % | `Usage ÷ Total`, capped at 100 |
| Data resets | sensor | date | When the allowance next resets |
| Wallet balance | sensor | GBP | Out-of-bundle balance |
| Out-of-bundle spend | sensor | GBP | Spend outside the plan this period |
| Number port status | sensor | — | Whether your number has been transferred, in Mozillion's own words |
| SIM status | sensor | — | The service status Mozillion reports, usually `ACTIVE` |
| Unlimited | binary sensor | — | On when the plan has no data cap |
| Overspend limit reached | binary sensor | — | On when spend outside the plan hits the wallet limit |

The wallet figures and the port status are **unavailable** rather than misleading
when they have nothing to say: a SIM with no wallet answers all zeros with
`reached: true`, and a SIM whose number was never ported reports no status.

The SIM status sensor carries the rest of what the dashboard publishes as
attributes: the plan (`plan_tariff`, `plan_duration`, `plan_roaming`,
`plan_texts_minutes`, `plan_is_data_only`, `plan_voicemail`,
`plan_parental_control`), the porting detail (`port_status`,
`port_status_description`, `port_date`), the billing fields (`billing_amount`,
`has_bill`, `billing_days`), and the raw payload. The four usage sensors also
expose a per-bucket breakdown (`usage_gbr` / `total_gbr`, `usage_global` /
`total_global`). See [Known limitations](#known-limitations).

## Data updates

Home Assistant polls Mozillion on an interval (default **1 hour**, configurable
under *Configure* → *Mozillion options*).

Each poll is two steps, because Mozillion regenerates the figures server-side:

1. `GET /get-data-usage?order_detail_id=…&sim_meta_id=…` kicks off a refresh.
   Mozillion usually answers `{"status": "pending"}`.
2. While the answer is pending, `GET /get-data-usage-status/<sim_meta_id>` is
   polled (up to 5 attempts, 3 seconds apart, the same cadence the dashboard
   uses) until it returns `{"status": "success", …}` with the figures.

The plan, SIM service status and data reset date come from a 355 KB dashboard page
rather than an endpoint. None of them change hourly (the reset label is monthly), so
that page is refreshed every **6 hours** while the small JSON calls keep the full poll
interval. The last good reading is kept if a refresh fails, and three failed refreshes
in a row raise a repair.

If the session has expired the integration logs in again and retries once.
Credentials that are actually rejected raise a re-authentication prompt rather
than retrying forever. A network failure leaves the previous values in place and
the entities go unavailable until the next successful poll.

## Use cases

- **Avoid a surprise data bill** — automate a warning when usage passes a
  threshold.
- **Dashboard tile** — show remaining data next to your phone's battery.
- **Log the trend** — the sensors use the `measurement` state class, so they
  work with the Statistics and History dashboards.
- **Track an unused SIM** — get told if a spare SIM starts consuming data.
- **Watch a number port finish** — the port status sensor follows Mozillion's own
  status from pending to done, so you know when to swap the SIM into your phone.

## Examples

Warn when less than 10% of the allowance is left:

```yaml
automation:
  - alias: Mozillion data running low
    triggers:
      - trigger: numeric_state
        entity_id: sensor.mozillion_07700900000_usage_percentage
        above: 90
    actions:
      - action: notify.persistent_notification
        data:
          title: Mozillion data
          message: >
            {{ states('sensor.mozillion_07700900000_remaining') }} GB left of
            {{ states('sensor.mozillion_07700900000_total') }} GB.
```

Show the remaining allowance on a dashboard:

```yaml
type: entities
entities:
  - entity: sensor.mozillion_07700900000_usage
  - entity: sensor.mozillion_07700900000_remaining
  - entity: sensor.mozillion_07700900000_usage_percentage
  - entity: binary_sensor.mozillion_07700900000_unlimited
```

> Entity ids depend on the device name, which is the SIM's phone number. Check
> *Settings → Devices & Services → Mozillion* for the exact ids.

### Blueprints

Rather than writing the automation yourself, two blueprints ship in this repository.
Import them from *Settings → Automations & Scenes → Blueprints → Import Blueprint*:

| Blueprint | Import URL |
| --- | --- |
| Data running low | `https://github.com/KRoperUK/mozillion-hass/blob/main/blueprints/automation/mozillion/data_running_low.yaml` |
| Overspend limit reached | `https://github.com/KRoperUK/mozillion-hass/blob/main/blueprints/automation/mozillion/overspend_limit_reached.yaml` |

Both take the relevant entity for the SIM plus the notify action to call, so one
blueprint covers every SIM you add.

## Known limitations

- **The wallet figures are assumed to be pounds.** Mozillion is a UK service and
  the site's own monetary attributes are in pounds — `data-amount="10.00"` sits
  beside a "wallet £10" label — but the overspend endpoint's `balance` and `spent`
  state neither a currency nor a scale, and a wallet that has never been topped up
  reads zero, so there has been nothing to check the scale against. If the figures
  ever look 100x out, this is the assumption to question first.
- **The billing figures are unverified.** `billing_amount` is passed through
  exactly as the page publishes it. The page states no currency and does not say
  whether the value is in pounds or pence, and the copy beside it only reads
  "Paid in full / No upcoming bill", so it is exposed as a raw attribute and no
  unit is claimed.
- **The per-bucket figures are unverified.** Mozillion returns `usedData`/
  `totalData` alongside `usedDataGbr`/`totalDataGbr` and `usedDataGlobal`/
  `totalDataGlobal`. The headline pair drives the sensors because that is what
  the website renders, but which roaming bucket each pair represents has not
  been confirmed against Mozillion's own documentation, so the buckets are
  exposed as attributes rather than entities of their own.
- **The SIM list is scraped from HTML.** Mozillion has no public API for the
  dashboard. If the page markup changes, setup falls back to manual entry of the
  order detail id and SIM meta id.
- **A cookie-only entry cannot renew itself.** When the session expires you are
  asked to re-authenticate; supply email/password to get transparent renewal.
- **The reset date is inferred.** The dashboard gives a day and month with no year
  (`19 Oct`). Because Mozillion's resets recur monthly — the label moves on to the
  next month once a reset passes — the date shown is the next occurrence of that day.
  The raw label and the day count are exposed as attributes on the sensor, so
  Mozillion's own wording is never hidden.
- **Your plan's allowance is not the allowance you can use abroad.** Mozillion caps
  how much of a plan's data can be used in the EU, and that cap varies by plan and is
  not exposed by the dashboard page or the usage endpoints. `Remaining` therefore
  reflects the plan's own allowance, not your remaining roaming data — check
  [Mozillion's roaming guidance](https://www.mozillion.com/resources/help/roaming-travel/)
  before relying on it while abroad.
- **Usage figures refresh as often as Mozillion regenerates them.** Polling more
  often than the provider updates will not produce new numbers.
- **This integration is unofficial** and is not affiliated with Mozillion.

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
