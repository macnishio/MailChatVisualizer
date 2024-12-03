"""update_contact_and_email_message_models

Revision ID: 215442fdfd3d
Revises: a0808c78ca16
Create Date: 2024-12-03 00:16:27.988260

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '215442fdfd3d'
down_revision = 'a0808c78ca16'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('contact', schema=None) as batch_op:
        batch_op.drop_constraint('contact_email_key', type_='unique')
        batch_op.drop_constraint('contact_normalized_email_key', type_='unique')
        batch_op.create_index('idx_contact_display_name', ['display_name'], unique=False)
        batch_op.create_index('idx_contact_email', ['email'], unique=False)
        batch_op.create_index('idx_contact_normalized_email', ['normalized_email'], unique=False)
        batch_op.create_unique_constraint('uq_contact_normalized_email', ['normalized_email'])

    with op.batch_alter_table('email_message', schema=None) as batch_op:
        batch_op.drop_index('idx_email_content')
        batch_op.drop_index('ix_email_message_from_address')
        batch_op.drop_index('ix_email_message_from_contact')
        batch_op.drop_index('ix_email_message_to_address')
        batch_op.drop_index('ix_email_message_to_contact')
        batch_op.create_index('idx_email_message_addresses', ['from_address', 'to_address'], unique=False)
        batch_op.create_index('idx_email_message_content', ['subject', 'body'], unique=False)
        batch_op.create_index('idx_email_message_date_folder', ['date', 'folder'], unique=False)
        batch_op.create_index('idx_email_message_from_contact', ['from_contact_id'], unique=False)
        batch_op.create_index('idx_email_message_to_contact', ['to_contact_id'], unique=False)

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('email_message', schema=None) as batch_op:
        batch_op.drop_index('idx_email_message_to_contact')
        batch_op.drop_index('idx_email_message_from_contact')
        batch_op.drop_index('idx_email_message_date_folder')
        batch_op.drop_index('idx_email_message_content')
        batch_op.drop_index('idx_email_message_addresses')
        batch_op.create_index('ix_email_message_to_contact', ['to_contact_id'], unique=False)
        batch_op.create_index('ix_email_message_to_address', ['to_address'], unique=False)
        batch_op.create_index('ix_email_message_from_contact', ['from_contact_id'], unique=False)
        batch_op.create_index('ix_email_message_from_address', ['from_address'], unique=False)
        batch_op.create_index('idx_email_content', ['subject', 'body'], unique=False)

    with op.batch_alter_table('contact', schema=None) as batch_op:
        batch_op.drop_constraint('uq_contact_normalized_email', type_='unique')
        batch_op.drop_index('idx_contact_normalized_email')
        batch_op.drop_index('idx_contact_email')
        batch_op.drop_index('idx_contact_display_name')
        batch_op.create_unique_constraint('contact_normalized_email_key', ['normalized_email'])
        batch_op.create_unique_constraint('contact_email_key', ['email'])

    # ### end Alembic commands ###