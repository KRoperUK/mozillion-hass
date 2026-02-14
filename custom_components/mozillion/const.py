"""Constants for the Mozillion integration."""

DOMAIN = "mozillion"
PLATFORMS = ["sensor", "binary_sensor"]
DEFAULT_BASE_URL = "https://www.mozillion.com/get-data-usage"
STATUS_BASE_URL = "https://www.mozillion.com/get-data-usage-status"
BASE_URL = "https://www.mozillion.com"
CONF_ORDER_DETAIL_ID = "order_detail_id"
CONF_SIM_PLAN_ID = "sim_plan_id"
CONF_SIM_NUMBER = "sim_number"
CONF_SESSION_COOKIE = "session_cookie"
CONF_XSRF_TOKEN = "xsrf_token"
CONF_USAGE_KEY = "usage_key"
CONF_REMAINING_KEY = "remaining_key"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_TOTP_SECRET = "totp_secret"
CONF_ORIGIN = "origin"
DEFAULT_USAGE_KEY = "usedData"
DEFAULT_REMAINING_KEY = "totalData"
DEFAULT_SCAN_INTERVAL = 86400
DEFAULT_ORIGIN = "https://www.mozillion.com"

ATTR_RAW = "raw"
ATTR_USAGE = "usage"
ATTR_REMAINING = "remaining"
ATTR_SIM_NUMBER = "sim_number"
ATTR_TOTAL = "total"
ATTR_USAGE_PERCENTAGE = "usage_percentage"
ATTR_UNLIMITED = "unlimited"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36"
)
