"""Instance-wide, administrator-maintained question constraints."""
from sqlalchemy import Boolean, Column, String, false
from sqlmodel import Field, SQLModel

CONSTRAINT_CODES = ('table_selection', 'metric_definition', 'business_time', 'month_range', 'empty_result')
MAX_CONTENT_LENGTH = 2000


class QuestionConstraint(SQLModel, table=True):
    __tablename__ = 'ops_question_constraint'
    code: str = Field(sa_column=Column(String(40), primary_key=True))
    name: str = Field(max_length=100)
    content: str = Field(max_length=MAX_CONTENT_LENGTH)
    enabled: bool = Field(default=False, sa_column=Column(Boolean, nullable=False, server_default=false()))
