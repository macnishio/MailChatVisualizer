"""empty message

Revision ID: aec5a2b41cc9
Revises: 4f5a42def456
Create Date: 2024-12-03 20:16:36.917243

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'aec5a2b41cc9'
down_revision = '4f5a42def456'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('contact', schema=None) as batch_op:
        batch_op.drop_index('idx_contact_normalized_email')
        batch_op.create_index('idx_contact_normalized_email', ['normalized_email'], unique=False)

    with op.batch_alter_table('email_message', schema=None) as batch_op:
        batch_op.drop_index('idx_email_message_partial_body')
        batch_op.drop_index('idx_email_message_subject')
        batch_op.create_index('idx_email_message_content', ['subject', 'body'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('email_message', schema=None) as batch_op:
        batch_op.drop_index('idx_email_message_content')
        batch_op.create_index('idx_email_message_subject', ['subject'], unique=False)
        batch_op.create_index('idx_email_message_partial_body', [sa.text('"substring"(body, 1, 1000)')], unique=False)

    with op.batch_alter_table('contact', schema=None) as batch_op:
        batch_op.drop_index('idx_contact_normalized_email')
        batch_op.create_index('idx_contact_normalized_email', ['normalized_email'], unique=True)

    # ### end Alembic commands ###
