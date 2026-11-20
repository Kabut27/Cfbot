"""Central configuration (env vars + constants)."""
import os

TOKEN = os.environ.get("BOT_TOKEN", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
DATA_DIR = os.environ.get("DATA_DIR", "data")
DB_PATH = os.path.join(DATA_DIR, "cfbot.db")
BACKUPS = os.path.join(DATA_DIR, "backups")
CLAIM_CODE = os.environ.get("CLAIM_CODE", "")
EXTRA_ADMINS = {int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip()}

FREE_TIER_GB = int(os.environ.get("FREE_TIER_GB", "1024"))
USAGE_ALERT_PCTS = (80, 95)
KEEP_BACKUPS = 10

# CloudFront managed policies
CACHING_DISABLED = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
CACHING_OPTIMIZED = "658327ea-f89d-4fab-a63d-7e88639e58f6"
ALL_VIEWER = "216adef6-5c7f-47e4-b989-5492eafa07d3"
CF_PREFIX_LIST_NAME = "com.amazonaws.global.cloudfront.origin-facing"
