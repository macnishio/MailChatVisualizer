"""optimize_indexes_and_migrate_contacts

Revision ID: 4f5a42def456
Revises: 3f5a42abc123
Create Date: 2024-12-03 20:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = '4f5a42def456'
down_revision = '3f5a42abc123'
branch_labels = None
depends_on = None

def upgrade():
    # インデックスの最適化
    with op.batch_alter_table('email_message', schema=None) as batch_op:
        # 既存の大きすぎるインデックスを削除
        batch_op.drop_index('idx_email_message_content')
        
        # より効率的な検索用インデックスを作成
        batch_op.create_index('idx_email_message_subject', ['subject'])
        batch_op.create_index('idx_email_message_partial_body', 
                            [sa.text('substring(body, 1, 1000)')])

    # 一時テーブルの作成
    op.create_table('temp_contacts',
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('normalized_email', sa.String(255), nullable=False),
        sa.Column('display_name', sa.String(255)),
        sa.Column('created_at', sa.DateTime, default=datetime.utcnow),
        sa.Column('updated_at', sa.DateTime, default=datetime.utcnow)
    )

    # トランザクション内でデータ移行を実行
    connection = op.get_bind()
    
    try:
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

        # Contactsテーブルへの移行（重複を防ぐ）
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

        # EmailMessageの外部キー参照を更新
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

    except Exception as e:
        raise e
    finally:
        # 一時テーブルの削除
        op.drop_table('temp_contacts')

def downgrade():
    # インデックスを元に戻す
    with op.batch_alter_table('email_message', schema=None) as batch_op:
        batch_op.drop_index('idx_email_message_subject')
        batch_op.drop_index('idx_email_message_partial_body')
        batch_op.create_index('idx_email_message_content', ['subject', 'body'])

    # 外部キーをNULLに設定（データを保持しつつ関連を解除）
    connection = op.get_bind()
    connection.execute(text("""
        UPDATE email_message
        SET from_contact_id = NULL,
            to_contact_id = NULL
    """))
