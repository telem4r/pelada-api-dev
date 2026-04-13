"""fix match_player_stats_v2 team_number constraint for manual ranking

Revision ID: 0073_fix_match_player_stats_v2_team_number_manual_ranking
Revises: 0072_add_manual_ranking_fields_to_match_player_stats_v2
Create Date: 2026-04-04 00:30:00
"""

from alembic import op


revision = "0073_fix_match_player_stats_v2_team_number_manual_ranking"
down_revision = "0072_add_manual_ranking_fields_to_match_player_stats_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            _constraint_name text;
        BEGIN
            SELECT con.conname
              INTO _constraint_name
              FROM pg_constraint con
              JOIN pg_class rel
                ON rel.oid = con.conrelid
              JOIN pg_namespace nsp
                ON nsp.oid = con.connamespace
             WHERE rel.relname = 'match_player_stats_v2'
               AND con.contype = 'c'
               AND pg_get_constraintdef(con.oid) ILIKE '%team_number%';

            IF _constraint_name IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE match_player_stats_v2 DROP CONSTRAINT %I',
                    _constraint_name
                );
            END IF;
        END $$;
        """
    )
    op.create_check_constraint(
        "ck_match_player_stats_v2_team_number",
        "match_player_stats_v2",
        "team_number >= 0",
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE match_player_stats_v2
        DROP CONSTRAINT IF EXISTS ck_match_player_stats_v2_team_number
        """
    )
    op.create_check_constraint(
        "ck_match_player_stats_v2_team_number",
        "match_player_stats_v2",
        "team_number > 0",
    )
