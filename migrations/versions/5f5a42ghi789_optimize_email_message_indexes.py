"""optimize_email_message_indexes

Revision ID: 5f5a42ghi789
Revises: 4f5a42def456
Create Date: 2024-12-03 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from datetime import datetime
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision = '5f5a42ghi789'
down_revision = '4f5a42def456'
branch_labels = None
depends_on = None

def upgrade():
    # トランザクション内で実行
    connection = op.get_bind()
    
    try:
        # インデックスの最適化
        with op.batch_alter_table('email_message', schema=None) as batch_op:
            # 既存の大きいインデックスを削除
            batch_op.drop_index('idx_email_message_content')
            
            # より効率的な検索用インデックスを作成
            batch_op.create_index('idx_email_message_subject_trgm', 
                                ['subject'],
                                postgresql_using='gin',
                                postgresql_ops={'subject': 'gin_trgm_ops'})
            
            batch_op.create_index('idx_email_message_body_trgm',
                                ['body'],
                                postgresql_using='gin',
                                postgresql_ops={'body': 'gin_trgm_ops'})

        # 一時テーブルの作成
        connection.execute(text("""
            CREATE TEMP TABLE temp_contacts AS
            SELECT DISTINCT ON (normalized_email)
                email,
                normalized_email,
                display_name,
                CURRENT_TIMESTAMP as created_at,
                CURRENT_TIMESTAMP as updated_at
            FROM (
                SELECT 
                    COALESCE(from_address, to_address) as email,
                    LOWER(REGEXP_REPLACE(COALESCE(from_address, to_address), '\s+', '')) as normalized_email,
                    COALESCE(
                        REGEXP_REPLACE(COALESCE(from_address, to_address), '<.*>$', ''),
                        COALESCE(from_address, to_address)
                    ) as display_name
                FROM email_message
                WHERE from_address IS NOT NULL OR to_address IS NOT NULL
            ) as subquery
            ORDER BY normalized_email, created_at DESC
        """))

        # 正規化されたデータをContactsテーブルに移行
        connection.execute(text("""
            INSERT INTO contact (email, normalized_email, display_name, created_at, updated_at)
            SELECT 
                email,
                normalized_email,
                display_name,
                created_at,
                updated_at
            FROM temp_contacts
            ON CONFLICT (normalized_email) DO UPDATE
            SET 
                email = EXCLUDED.email,
                display_name = EXCLUDED.display_name,
                updated_at = CURRENT_TIMESTAMP
        """))

        # EmailMessageの関連付けを更新
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

        # インデックスの作成
        with op.batch_alter_table('contact', schema=None) as batch_op:
            batch_op.create_index('idx_contact_email_trgm',
                                ['email'],
                                postgresql_using='gin',
                                postgresql_ops={'email': 'gin_trgm_ops'})
            
            batch_op.create_index('idx_contact_display_name_trgm',
                                ['display_name'],
                                postgresql_using='gin',
                                postgresql_ops={'display_name': 'gin_trgm_ops'})

    except Exception as e:
        raise Exception(f"Migration failed: {str(e)}")

def downgrade():
    # トランザクション内で実行
    connection = op.get_bind()
    
    try:
        # インデックスを元に戻す
        with op.batch_alter_table('email_message', schema=None) as batch_op:
            batch_op.drop_index('idx_email_message_subject_trgm')
            batch_op.drop_index('idx_email_message_body_trgm')
            batch_op.create_index('idx_email_message_content',
                                ['subject', 'body'])

        with op.batch_alter_table('contact', schema=None) as batch_op:
            batch_op.drop_index('idx_contact_email_trgm')
            batch_op.drop_index('idx_contact_display_name_trgm')

    except Exception as e:
        raise Exception(f"Downgrade failed: {str(e)}")
