"""add workflow prompts, dpi, expected json and result artifacts

Revision ID: 20260716_workflows
Revises: 108853119b33
Create Date: 2026-07-16
"""
from alembic import op
import sqlalchemy as sa

revision = '20260716_workflows'
down_revision = '108853119b33'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('prompts') as batch:
        batch.add_column(sa.Column('system_prompt', sa.Text(), nullable=False, server_default=''))
        batch.add_column(sa.Column('expected_json', sa.Text(), nullable=True))
    with op.batch_alter_table('prompt_runs') as batch:
        batch.add_column(sa.Column('workflow', sa.String(length=32), nullable=False, server_default='count'))
        batch.add_column(sa.Column('dpi', sa.Integer(), nullable=False, server_default='200'))
        batch.add_column(sa.Column('result_json', sa.Text(), nullable=True))
        batch.add_column(sa.Column('artifacts_path', sa.String(length=512), nullable=True))


def downgrade():
    with op.batch_alter_table('prompt_runs') as batch:
        batch.drop_column('artifacts_path')
        batch.drop_column('result_json')
        batch.drop_column('dpi')
        batch.drop_column('workflow')
    with op.batch_alter_table('prompts') as batch:
        batch.drop_column('expected_json')
        batch.drop_column('system_prompt')
