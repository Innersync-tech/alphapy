"""Add reminders.completed for dashboard mark-done UX

Revision ID: 026_reminders_completed_flag
Revises: 025_growth_checkins_content
Create Date: 2026-08-04

Dashboard historically added this column via runtime DDL. Promote it to
Alembic SoT so guild reminder APIs can mark one-offs complete without
Dashboard owning schema mutations.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "026_reminders_completed_flag"
down_revision: Union[str, None] = "025_growth_checkins_content"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE reminders ADD COLUMN IF NOT EXISTS completed BOOLEAN DEFAULT FALSE"
    )
    # If legacy Dashboard DDL left a separate scheduled_time column, copy
    # orphaned one-offs into Alembic SoT event_time.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'reminders' AND column_name = 'scheduled_time'
            ) THEN
                UPDATE reminders
                SET event_time = scheduled_time
                WHERE event_time IS NULL AND scheduled_time IS NOT NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE reminders DROP COLUMN IF EXISTS completed")
