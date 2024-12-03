"""migrate_email_contacts

Revision ID: 3f5a42abc123
Revises: 215442fdfd3d
Create Date: 2024-12-03 00:35:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime

# revision identifiers, used by Alembic.
revision = '3f5a42abc123'
down_revision = '215442fdfd3d'
branch_labels = None
depends_on = None

def upgrade():
    # 一時テーブルの作成
    op.create_table('temp_contacts',
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('normalized_email', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255)),
        sa.Column('created_at', sa.DateTime, default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    )

    # データ移行の実行
    from sqlalchemy import text
    connection = op.get_bind()
    
    # メールアドレスの抽出とContact作成
    connection.execute(text("""
        INSERT INTO temp_contacts (email, normalized_email, display_name)
        SELECT DISTINCT 
            COALESCE(from_address, to_address) as email,
            LOWER(REGEXP_REPLACE(COALESCE(from_address, to_address), '\s+', '')) as normalized_email,
            COALESCE(
                REGEXP_REPLACE(COALESCE(from_address, to_address), '<.*>$', ''),
                COALESCE(from_address, to_address)
            ) as display_name
        FROM email_message
        WHERE from_address IS NOT NULL OR to_address IS NOT NULL
    """))

    # 重複を排除しながらContactテーブルにデータを移行
    connection.execute(text("""
        INSERT INTO contact (email, normalized_email, display_name, created_at, updated_at)
        SELECT DISTINCT ON (normalized_email)
            email,
            normalized_email,
            display_name,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM temp_contacts
        ON CONFLICT (normalized_email) DO UPDATE
        SET updated_at = CURRENT_TIMESTAMP
    """))

    # EmailMessageの外部キーを更新
    connection.execute(text("""
        UPDATE email_message em
        SET from_contact_id = c.id
        FROM contact c
        WHERE LOWER(REGEXP_REPLACE(em.from_address, '\s+', '')) = c.normalized_email
        AND em.from_contact_id IS NULL
    """))

    connection.execute(text("""
        UPDATE email_message em
        SET to_contact_id = c.id
        FROM contact c
        WHERE LOWER(REGEXP_REPLACE(em.to_address, '\s+', '')) = c.normalized_email
        AND em.to_contact_id IS NULL
    """))

    # 一時テーブルの削除
    op.drop_table('temp_contacts')

def downgrade():
    # 外部キーをNULLに設定（データを保持しつつ関連を解除）
    connection = op.get_bind()
    connection.execute("""
        UPDATE email_message
        SET from_contact_id = NULL,
            to_contact_id = NULL
    """)
