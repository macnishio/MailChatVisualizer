"""empty message

Revision ID: aec5a2b41cc9
Revises: 4f5a42def456
Create Date: 2024-12-04 xx:xx:xx.xxxx

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

revision = 'aec5a2b41cc9'
down_revision = '4f5a42def456'
branch_labels = None
depends_on = None

def upgrade():
    # インデックスの安全な削除
    op.execute('DROP INDEX IF EXISTS idx_email_message_partial_body')

    # TSVectorカラムを追加
    op.add_column('email_message',
        sa.Column('body_tsv', postgresql.TSVECTOR, nullable=True)
    )

    # GINインデックスの作成
    op.execute(
        'CREATE INDEX IF NOT EXISTS idx_email_message_fulltext ON email_message USING gin(body_tsv)'
    )

    # トリガーの作成
    op.execute("""
        CREATE OR REPLACE FUNCTION email_message_tsvector_update() RETURNS trigger AS $$
        BEGIN
            NEW.body_tsv := to_tsvector('english', COALESCE(NEW.subject, '') || ' ' || COALESCE(NEW.body, ''));
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS email_message_tsvector_update ON email_message;
        CREATE TRIGGER email_message_tsvector_update
            BEFORE INSERT OR UPDATE ON email_message
            FOR EACH ROW
            EXECUTE FUNCTION email_message_tsvector_update();
    """)

    # 既存のデータを更新
    op.execute("""
        UPDATE email_message
        SET body_tsv = to_tsvector('english', COALESCE(subject, '') || ' ' || COALESCE(body, ''))
    """)

def downgrade():
    # トリガーの削除
    op.execute('DROP TRIGGER IF EXISTS email_message_tsvector_update ON email_message')
    op.execute('DROP FUNCTION IF EXISTS email_message_tsvector_update')

    # インデックスの削除
    op.execute('DROP INDEX IF EXISTS idx_email_message_fulltext')

    # TSVectorカラムの削除
    op.drop_column('email_message', 'body_tsv')