import os
from dotenv import load_dotenv
import warnings

# Suppress dotenv warnings about empty lines or trailing newlines in .env file
# These are harmless and common in .env files
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*dotenv.*")
    load_dotenv()  # Load variables from .env file

BOT_TOKEN = os.getenv("BOT_TOKEN")
# Optional: use a separate bot token for local testing (e.g. a dev bot). Set USE_TEST_BOT=1 to use this.
BOT_TOKEN_TEST = os.getenv("BOT_TOKEN_TEST")
# Token actually used to run the bot: BOT_TOKEN_TEST when USE_TEST_BOT=1 and BOT_TOKEN_TEST is set, else BOT_TOKEN.
BOT_TOKEN_ACTIVE = (BOT_TOKEN_TEST if os.getenv("USE_TEST_BOT") == "1" and BOT_TOKEN_TEST else None) or BOT_TOKEN
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")  # Legacy - kept for backwards compatibility
GROK_API_KEY = os.getenv("GROK_API_KEY")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "grok").strip().lower()  # "grok" or "openai"

ROLE_ID = int(os.getenv("ROLE_ID", "0"))  # Legacy - no longer used in multi-guild setup

# Database & API security
DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY")
DEFAULT_ALLOWED_ORIGINS = [
    "https://app.innersync.tech",
    "https://mind.innersync.tech",
    "https://alphapy.innersync.tech",
]
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
] or DEFAULT_ALLOWED_ORIGINS
APP_BASE_URL = os.getenv("APP_BASE_URL", DEFAULT_ALLOWED_ORIGINS[0])
MIND_BASE_URL = os.getenv("MIND_BASE_URL", DEFAULT_ALLOWED_ORIGINS[1])
ALPHAPY_BASE_URL = os.getenv("ALPHAPY_BASE_URL", DEFAULT_ALLOWED_ORIGINS[2])
SERVICE_NAME = os.getenv("SERVICE_NAME", "alphapy-service")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_JWKS_URL = os.getenv("SUPABASE_JWKS_URL") or (
    f"{SUPABASE_URL}/auth/v1/certs" if SUPABASE_URL else None
)
SUPABASE_JWT_AUDIENCE = os.getenv("SUPABASE_JWT_AUDIENCE", "authenticated")
SUPABASE_ISSUER = os.getenv(
    "SUPABASE_ISSUER",
    f"{SUPABASE_URL}/auth/v1" if SUPABASE_URL else None,
)
SUPABASE_WEBHOOK_SECRET = os.getenv("SUPABASE_WEBHOOK_SECRET")
# App reflections webhook (Core-API → Alphapy). Optional; falls back to WEBHOOK_SECRET / SUPABASE_WEBHOOK_SECRET
APP_REFLECTIONS_WEBHOOK_SECRET = os.getenv("APP_REFLECTIONS_WEBHOOK_SECRET")

# Google Drive service account JSON (full key file as one-line/minified JSON string)
GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON")

# Telemetry ingest configuration
TELEMETRY_INGEST_INTERVAL = int(os.getenv("TELEMETRY_INGEST_INTERVAL", "45"))  # seconds

# Core-API ingress (neural plane centralisation)
# When set, telemetry and operational events are sent to Core instead of direct Supabase.
# Use the Core API origin (e.g. https://core.innersync.tech), not a Next.js site such as api.innersync.tech.
CORE_API_URL = (os.getenv("CORE_API_URL") or "").rstrip("/")
ALPHAPY_SERVICE_KEY = os.getenv("ALPHAPY_SERVICE_KEY")
# Core-mediated Hermit strategic context
HERMIT_CONTEXT_ENABLED = os.getenv("HERMIT_CONTEXT_ENABLED", "false").strip().lower() == "true"
HERMIT_CONTEXT_TTL_SECONDS = int(os.getenv("HERMIT_CONTEXT_TTL_SECONDS", "1800"))
HERMIT_CONTEXT_TIMEOUT_SECONDS = float(os.getenv("HERMIT_CONTEXT_TIMEOUT_SECONDS", "2.0"))
CORE_HERMIT_CONTEXT_PATH = (os.getenv("CORE_HERMIT_CONTEXT_PATH") or "/integrations/hermit/strategic-context").strip()
HERMIT_STRATEGY_PACKET_ENABLED = os.getenv("HERMIT_STRATEGY_PACKET_ENABLED", "false").strip().lower() == "true"
HERMIT_EVENTS_ENABLED = os.getenv("HERMIT_EVENTS_ENABLED", "true").strip().lower() == "true"
HERMIT_EVENTS_WEBHOOK_SECRET = os.getenv("HERMIT_EVENTS_WEBHOOK_SECRET", "").strip()
# Alphapy multi-user agents (closed-loop runtime in bot)
ALPHAPY_AGENTS_ENABLED = os.getenv("ALPHAPY_AGENTS_ENABLED", "false").strip().lower() == "true"
# supabase = persist sessions/memory via service role; memory = in-process only (tests/dev)
ALPHAPY_AGENTS_MEMORY_BACKEND = (os.getenv("ALPHAPY_AGENTS_MEMORY_BACKEND") or "supabase").strip().lower()
# Core → Alphapy: confirm Discord ↔ Innersync link (HMAC via X-Webhook-Signature)
DISCORD_LINK_WEBHOOK_SECRET = os.getenv("DISCORD_LINK_WEBHOOK_SECRET")
# Optional path overrides for Core integration (defaults match planned Core routes)
CORE_DISCORD_LINK_SESSION_PATH = (os.getenv("CORE_DISCORD_LINK_SESSION_PATH") or "").strip()
CORE_DISCORD_BOT_PROFILE_PATH = (os.getenv("CORE_DISCORD_BOT_PROFILE_PATH") or "").strip()

# Discord OAuth2 for Web Configuration Interface
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_OAUTH_REDIRECT_URI = os.getenv("DISCORD_OAUTH_REDIRECT_URI", f"{ALPHAPY_BASE_URL}/api/auth/discord/callback")


# Main Guild (Primary server where bot operates)
# This is used as default for API endpoints when no guild_id is specified
MAIN_GUILD_ID = int(os.getenv("MAIN_GUILD_ID", "0"))  # Set to 0 to disable filtering (show all guilds)

# Premium tier
PREMIUM_CHECKOUT_URL = os.getenv("PREMIUM_CHECKOUT_URL", "")
PREMIUM_CACHE_TTL_SECONDS = int(os.getenv("PREMIUM_CACHE_TTL_SECONDS", "300"))
# Payments token for Core-API billing endpoints (e.g. early-bird validation)
CORE_API_PAYMENTS_TOKEN = os.getenv("CORE_API_PAYMENTS_TOKEN", "")
# Early bird settings
EARLY_BIRD_CODE = os.getenv("EARLY_BIRD_CODE", "EARLYBIRD50")
EARLY_BIRD_TOTAL_SPOTS = int(os.getenv("EARLY_BIRD_TOTAL_SPOTS", "50"))
# Prices shown in /premium embed (early bird and regular)
PRICE_MONTHLY = os.getenv("PRICE_MONTHLY", "€4.99")
PRICE_YEARLY_EARLY_BIRD = os.getenv("PRICE_YEARLY_EARLY_BIRD", "€29")
PRICE_YEARLY_REGULAR = os.getenv("PRICE_YEARLY_REGULAR", "€59.99")
PRICE_LIFETIME_EARLY_BIRD = os.getenv("PRICE_LIFETIME_EARLY_BIRD", "€49")
PRICE_LIFETIME_REGULAR = os.getenv("PRICE_LIFETIME_REGULAR", "€99.99")
# Webhook secrets for Core → Alphapy callbacks (optional; fall back to APP_REFLECTIONS_WEBHOOK_SECRET / WEBHOOK_SECRET)
PREMIUM_INVALIDATE_WEBHOOK_SECRET = os.getenv("PREMIUM_INVALIDATE_WEBHOOK_SECRET")
FOUNDER_WEBHOOK_SECRET = os.getenv("FOUNDER_WEBHOOK_SECRET")
# Legal update webhook (GitHub Action → Alphapy). Notifies main guild when PP/ToS changes.
LEGAL_UPDATE_WEBHOOK_SECRET = os.getenv("LEGAL_UPDATE_WEBHOOK_SECRET")
# Channel in the main guild to post legal update embeds. Falls back to system.log_channel_id if unset.
LEGAL_UPDATES_CHANNEL_ID = int(os.getenv("LEGAL_UPDATES_CHANNEL_ID", "0"))

# Alphapy dashboard (separate Next.js control panel for bot configuration)
DASHBOARD_BASE_URL = (os.getenv("DASHBOARD_BASE_URL") or "").rstrip("/")
# HMAC secrets for outbound signed webhooks from bot → dashboard
REFLECTION_WEBHOOK_SECRET = os.getenv("REFLECTION_WEBHOOK_SECRET")
GDPR_WEBHOOK_SECRET = os.getenv("GDPR_WEBHOOK_SECRET")

# GitHub (for /release notes and "read full" link). Optional; when unset, /release uses local changelog.
GITHUB_REPO = (os.getenv("GITHUB_REPO") or "").strip().rstrip("/")
# Optional: token for GitHub API (e.g. /release, repo links) to avoid rate limits
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

# Admin And Owner — set OWNER_IDS / ADMIN_ROLE_ID in your deployment env vars
# as comma-separated Discord snowflake IDs.
OWNER_IDS = [
    int(x.strip())
    for x in os.getenv("OWNER_IDS", "").split(",")
    if x.strip()
]
ADMIN_ROLE_ID = [
    int(x.strip())
    for x in os.getenv("ADMIN_ROLE_ID", "").split(",")
    if x.strip()
]

# Image reminder rate limiting
IMAGE_REMINDER_RATE_LIMIT_WINDOW = int(os.getenv("IMAGE_REMINDER_RATE_LIMIT_WINDOW", "3600"))  # seconds
IMAGE_REMINDER_RATE_LIMIT_COUNT = int(os.getenv("IMAGE_REMINDER_RATE_LIMIT_COUNT", "100"))  # max entries per user/guild
