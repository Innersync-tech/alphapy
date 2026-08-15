"""agent_nudge_state — delivery ledger for opt-in Discord DM check-in nudges.

Revision ID: 027_agent_nudge_state
Revises: 026_reminders_completed_flag
Create Date: 2026-08-15

Tracks last successful DM send per linked Innersync user so the hourly
nudge loop can enforce a 24h cooldown without writing Supabase prefs.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "027_agent_nudge_state"
down_revision: Union[str, None] = "026_reminders_completed_flag"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agent_nudge_state (
            innersync_user_id UUID NOT NULL,
            discord_user_id BIGINT NOT NULL,
            last_sent_at TIMESTAMPTZ NULL,
            CONSTRAINT agent_nudge_state_pkey PRIMARY KEY (innersync_user_id)
        );
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_nudge_state_discord "
        "ON agent_nudge_state(discord_user_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_nudge_state_last_sent "
        "ON agent_nudge_state(last_sent_at);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_nudge_state_last_sent;")
    op.execute("DROP INDEX IF EXISTS idx_agent_nudge_state_discord;")
    op.execute("DROP TABLE IF EXISTS agent_nudge_state;")
