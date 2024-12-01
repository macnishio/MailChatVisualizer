from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None

def upgrade():
    # 検索用のインデックスを作成
    op.create_index('idx_email_message_subject', 'email_message', ['subject'])
    op.create_index('idx_email_message_body', 'email_message', ['body'])
    op.create_index('idx_email_message_from_address', 'email_message', ['from_address'])
    op.create_index('idx_email_message_to_address', 'email_message', ['to_address'])
    op.create_index('idx_email_message_date', 'email_message', ['date'])

def downgrade():
    # インデックスを削除
    op.drop_index('idx_email_message_subject')
    op.drop_index('idx_email_message_body')
    op.drop_index('idx_email_message_from_address')
    op.drop_index('idx_email_message_to_address')
    op.drop_index('idx_email_message_date')
