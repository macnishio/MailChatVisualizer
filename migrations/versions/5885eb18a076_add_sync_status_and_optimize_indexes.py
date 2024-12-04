"""add_sync_status_and_optimize_indexes
Revision ID: 5885eb18a076
Revises: 206063017c9c
Create Date: 2024-12-04 18:20:19.934273
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '5885eb18a076'
down_revision = '206063017c9c'
branch_labels = None
depends_on = None

def upgrade():
    # EmailSettingsテーブルの拡張
    with op.batch_alter_table('email_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('last_sync_status', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('sync_error', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('is_syncing', sa.Boolean(), nullable=True))

    # メール日付に対する複合インデックスを追加
    op.create_index(
        'idx_email_message_folder_date',
        'email_message',
        ['folder', 'date'],
        postgresql_using='btree'
    )

    # 既存の単一の日付インデックスを削除（もし存在する場合）
    try:
        op.drop_index('ix_email_message_date', table_name='email_message')
    except:
        pass  # インデックスが存在しない場合は無視

def downgrade():
    # インデックスの復元
    op.create_index('ix_email_message_date', 'email_message', ['date'])
    op.drop_index('idx_email_message_folder_date')

    # 追加したカラムの削除
    with op.batch_alter_table('email_settings', schema=None) as batch_op:
        batch_op.drop_column('is_syncing')
        batch_op.drop_column('sync_error')
        batch_op.drop_column('last_sync_status')