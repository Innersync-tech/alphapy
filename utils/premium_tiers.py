"""
Central tier definitions for the premium system.

All tier-based limits and rank comparisons live here so they can be
imported by premium_guard, cogs, and any future feature without circular deps.
"""

# Tier rank: higher = more access. 'free' is the baseline (non-premium).
TIER_RANK: dict[str, int] = {
    "free": 0,
    "monthly": 1,
    "yearly": 2,
    "lifetime": 3,
}

# Daily GPT call limit per tier. None means unlimited.
GPT_DAILY_LIMIT: dict[str, int | None] = {
    "free": 5,
    "monthly": 25,
    "yearly": None,
    "lifetime": None,
}

# Maximum number of active reminders per (user, guild). None means unlimited.
REMINDER_LIMIT: dict[str, int | None] = {
    "free": 10,
    "monthly": None,
    "yearly": None,
    "lifetime": None,
}

# Daily /agent start cap per Discord user. None means unlimited.
AGENT_DAILY_SESSION_LIMIT: dict[str, int | None] = {
    "free": 10,
    "monthly": 25,
    "yearly": None,
    "lifetime": None,
}
