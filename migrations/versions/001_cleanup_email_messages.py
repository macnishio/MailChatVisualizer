from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    # メッセージテーブルのデータをクリア
    op.execute('TRUNCATE TABLE email_message RESTART IDENTITY CASCADE')

def downgrade():
    pass
