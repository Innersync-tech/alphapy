"""growth_checkins: store check-in content for /growthhistory

Revision ID: 025_growth_checkins_content
Revises: 024_agent_session_usage
Create Date: 2026-07-15

Adds goal/obstacle/feeling/grok_response so Discord /growthhistory and the
control-panel Growth tab can show plaintext check-ins. Content must not be
read from Supabase reflections (App vault may be zero-knowledge encrypted).
"""

from typing import Sequence, Union

from alembic import op


revision: str = "025_growth_checkins_content"
down_revision: Union[str, None] = "024_agent_session_usage"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE growth_checkins
            ADD COLUMN IF NOT EXISTS goal TEXT,
            ADD COLUMN IF NOT EXISTS obstacle TEXT,
            ADD COLUMN IF NOT EXISTS feeling TEXT,
            ADD COLUMN IF NOT EXISTS grok_response TEXT
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_growth_checkins_user_created "
        "ON growth_checkins (user_id, created_at DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF NOT EXISTS idx_growth_checkins_user_created")
    op.execute(
        """
        ALTER TABLE growth_checkins
            DROP COLUMN IF EXISTS goal,
            DROP COLUMN IF EXISTS obstacle,
            DROP COLUMN IF EXISTS feeling,
            DROP COLUMN IF EXISTS grok_response
        """
    )
