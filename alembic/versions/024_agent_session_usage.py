"""Add agent_session_usage table for per-user daily /agent start quota

Revision ID: 024_agent_session_usage
Revises: 023_alphapy_discord_links
Create Date: 2026-06-30

Tracks how many agent sessions each Discord user starts per day.
Used by check_and_increment_agent_session_quota() in utils/premium_guard.py.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "024_agent_session_usage"
down_revision: Union[str, None] = "023_alphapy_discord_links"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_session_usage (
            user_id       BIGINT  NOT NULL,
            usage_date    DATE    NOT NULL DEFAULT CURRENT_DATE,
            session_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, usage_date)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_session_usage_date ON agent_session_usage (usage_date)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_session_usage_date")
    op.execute("DROP TABLE IF EXISTS agent_session_usage")
