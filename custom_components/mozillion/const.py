"""Constants for the Mozillion integration."""

DOMAIN = "mozillion"

# The config entry is the Mozillion *account*; each SIM on it is a subentry of
# this type, so credentials are stored once however many SIMs are tracked.
SUBENTRY_TYPE_SIM = "sim"

BASE_URL = "https://www.mozillion.com"
LOGIN_PATH = "/login"
LOGIN_POST_PATH = "/login-post"
TWO_FACTOR_PATH = "/2fa/verify"
DASHBOARD_PATH = "/new-user-dashboard"
DATA_USAGE_PATH = "/get-data-usage"
DATA_USAGE_STATUS_PATH = "/get-data-usage-status"
# Where the dashboard's own wallet button reads the overspend balance from.
CHECK_BALANCE_PATH = "/overspend-limits/check-balance"

CONF_ORDER_DETAIL_ID = "order_detail_id"
CONF_SIM_META_ID = "sim_meta_id"
CONF_SIM_NUMBER = "sim_number"
CONF_ICCID = "iccid"
CONF_SESSION_COOKIE = "session_cookie"
CONF_XSRF_TOKEN = "xsrf_token"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TOTP_SECRET = "totp_secret"
CONF_ORIGIN = "origin"

# Mozillion regenerates usage server-side and answers "pending" until the
# numbers are ready, so each poll mirrors the dashboard's own polling loop
# (five attempts, three seconds apart).
USAGE_POLL_ATTEMPTS = 5
USAGE_POLL_DELAY = 3.0

DEFAULT_SCAN_INTERVAL = 3600
DEFAULT_ORIGIN = BASE_URL

# Proactively re-authenticate once a session is older than this, so we never
# poll with an already-expired cookie/token.
AUTH_REFRESH_THRESHOLD = 43200  # 12 hours, in seconds

# The plan, SIM status and reset date come from a 355 KB dashboard page. None of
# them change hourly (the reset label is monthly), so the page is refreshed on
# its own slower cadence while the JSON usage and wallet calls keep the poll
# interval. Also reduces how often we spend part of Mozillion's request budget.
DASHBOARD_REFRESH_INTERVAL = 21600  # 6 hours, in seconds

# The dashboard markup is the integration's one genuinely fragile dependency;
# raise a repair once reading it has failed this many polls in a row, so a
# single blip does not nag the user.
REPAIR_FAILURE_THRESHOLD = 3
ISSUE_DASHBOARD_UNREADABLE = "dashboard_unreadable"

ATTR_RAW = "raw"
ATTR_USAGE = "usage"
ATTR_TOTAL = "total"
ATTR_REMAINING = "remaining"
ATTR_USAGE_PERCENTAGE = "usage_percentage"
ATTR_UNLIMITED = "unlimited"
ATTR_SIM_NUMBER = "sim_number"
ATTR_ICCID = "iccid"
ATTR_USAGE_GBR = "usage_gbr"
ATTR_TOTAL_GBR = "total_gbr"
ATTR_USAGE_GLOBAL = "usage_global"
ATTR_TOTAL_GLOBAL = "total_global"

# From the dashboard's SIM markup.
ATTR_SIM_STATUS = "sim_status"
ATTR_RESET_DATE = "reset_date"
ATTR_RESET_LABEL = "reset_label"
ATTR_DAYS_LEFT = "days_left"
ATTR_PLAN_TARIFF = "plan_tariff"
ATTR_PLAN_DURATION = "plan_duration"
ATTR_PLAN_ROAMING = "plan_roaming"
ATTR_PLAN_TEXTS = "plan_texts_minutes"
ATTR_PLAN_IS_DATA_ONLY = "plan_is_data_only"
ATTR_PLAN_VOICEMAIL = "plan_voicemail"
ATTR_PLAN_PARENTAL_CONTROL = "plan_parental_control"
ATTR_PORT_STATUS = "port_status"
ATTR_PORT_STATUS_LABEL = "port_status_label"
ATTR_PORT_STATUS_DESCRIPTION = "port_status_description"
ATTR_PORT_DATE = "port_date"
ATTR_BILLING_AMOUNT = "billing_amount"
ATTR_HAS_BILL = "has_bill"
ATTR_BILLING_DAYS = "billing_days"

# From the overspend (wallet) endpoint.
ATTR_WALLET = "wallet"
ATTR_WALLET_BALANCE = "wallet_balance"
ATTR_WALLET_SPEND = "wallet_spend"
ATTR_OVERSPEND_LIMIT_REACHED = "overspend_limit_reached"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36"
)
