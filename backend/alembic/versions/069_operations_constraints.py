"""Seed editable question constraints without replacing existing content."""
from alembic import op
import sqlalchemy as sa

revision = 'b7e066b1c004'
down_revision = 'b7e066b1c003'
branch_labels = None
depends_on = None

# Keep release seeds in this migration so later application edits cannot alter it.
TEMPLATES = (
    ('table_selection', '选表与目录判断',
     '选表应综合表名、表注释、字段说明和已确认的业务同义词，并限定在本次提供的候选范围内。'
     '未选中目标表不等于数据库中不存在该表；候选不足或语义不明确时，应说明无法确认并请求澄清，不能编造表名。'),
    ('metric_definition', '指标与金额口径',
     '指标遵循已经确认的业务定义、计算公式、统计粒度和单位。不能仅凭金额、单价、数量或税率等列名猜测计算公式。'
     '口径不明时明确缺少的信息，不能将可执行 SQL 当作业务口径正确的证据。'),
    ('business_time', '业务日期与采集日期',
     '区分业务发生时间与合并、抽取、同步等技术时间。业务统计使用该指标经确认的业务日期；'
     '不能为得到结果而改用采集或同步日期。用户明确询问采集、同步情况时才使用相应技术日期；字段含义不明确时请求确认。'),
    ('month_range', '月份时间范围',
     '按月份查询使用包含月初、不包含下月月初的半开区间，例如 2026 年 8 月对应 '
     '[2026-08-01, 2026-09-01)。根据已确认的字段类型、日期格式和业务时区构造条件；'
     '不能对未知字符串日期格式猜测转换，也不能遗漏月末带时间的数据。'),
    ('empty_result', '空结果与零值解释',
     '空结果不能通过擅自删除时间或其他过滤条件、换表或替换字段来凑出答案。区分返回 0 行、返回行全部为 NULL '
     '和返回有效数值 0；0 是有效值，NULL 不等于 0，聚合 NULL 不能单独证明底层没有行。'
     '权限拒绝、检索失败和执行异常也不能表述为业务没有数据，应如实说明限制或请用户确认口径。'),
)


def upgrade():
    op.create_table('ops_question_constraint',
        sa.Column('code', sa.String(40), primary_key=True),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('content', sa.String(2000), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        if_not_exists=True)
    for code, name, content in TEMPLATES:
        op.execute(sa.text('INSERT INTO ops_question_constraint (code, name, content, enabled) '
            'VALUES (:code, :name, :content, false) ON CONFLICT (code) DO NOTHING')
            .bindparams(code=code, name=name, content=content))


def downgrade():
    op.drop_table('ops_question_constraint')
