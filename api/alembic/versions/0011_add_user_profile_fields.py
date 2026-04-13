"""add user profile fields

Sprint 2 - Perfil
- Dados pessoais
- Dados do jogador (posição / pé preferido)
- Idioma
- Time de coração
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0011_add_user_profile_fields"
down_revision = "0010_group_finance_fines2"
branch_labels = None
depends_on = None


def upgrade():
    # Enums (Postgres)
    player_position_enum = postgresql.ENUM(
        "GOLEIRO",
        "DEFENSOR",
        "MEIO",
        "ATACANTE",
        name="player_position",
    )
    preferred_foot_enum = postgresql.ENUM(
        "DIREITO",
        "ESQUERDO",
        "AMBIDESTRO",
        name="preferred_foot",
    )

    bind = op.get_bind()
    player_position_enum.create(bind, checkfirst=True)
    preferred_foot_enum.create(bind, checkfirst=True)

    # Campos do perfil
    op.add_column("users", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("birth_date", sa.Date(), nullable=True))

    op.add_column("users", sa.Column("favorite_team", sa.String(length=120), nullable=True))

    op.add_column("users", sa.Column("birth_country", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("birth_state", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("birth_city", sa.String(length=120), nullable=True))

    op.add_column("users", sa.Column("current_country", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("current_state", sa.String(length=100), nullable=True))
    op.add_column("users", sa.Column("current_city", sa.String(length=120), nullable=True))

    op.add_column(
        "users",
        sa.Column(
            "position",
            postgresql.ENUM(name="player_position", create_type=False),
            nullable=True,
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "preferred_foot",
            postgresql.ENUM(name="preferred_foot", create_type=False),
            nullable=True,
        ),
    )

    op.add_column("users", sa.Column("language", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("updated_profile_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Remover colunas
    op.drop_column("users", "updated_profile_at")
    op.drop_column("users", "language")
    op.drop_column("users", "preferred_foot")
    op.drop_column("users", "position")

    op.drop_column("users", "current_city")
    op.drop_column("users", "current_state")
    op.drop_column("users", "current_country")

    op.drop_column("users", "birth_city")
    op.drop_column("users", "birth_state")
    op.drop_column("users", "birth_country")

    op.drop_column("users", "favorite_team")
    op.drop_column("users", "birth_date")
    op.drop_column("users", "last_name")
    op.drop_column("users", "first_name")

    # Enums
    bind = op.get_bind()
    postgresql.ENUM(name="player_position").drop(bind, checkfirst=True)
    postgresql.ENUM(name="preferred_foot").drop(bind, checkfirst=True)
