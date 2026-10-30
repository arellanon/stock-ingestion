"""Initial portable schema. Frozen snapshot isolates migration history from model changes."""
from alembic import op
from migrations_snapshot import metadata
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

def upgrade(): metadata.create_all(op.get_bind())
def downgrade(): metadata.drop_all(op.get_bind())
