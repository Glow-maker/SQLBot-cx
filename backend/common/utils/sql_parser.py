"""
主线二 Phase 3a：SQL 表名解析工具

用 sqlglot 从最终要执行的 SQL 中解析出真实涉及的表名集合，用于双 Gate 权限校验。

为什么不能只信 LLM 返回的 tables：
- LLM 可能漏报 join / CTE / 子查询 / union 中的表
- generate_filter / generate_assistant_filter / check_save_sql 可能二次改写 SQL
- 动态数据源场景下 real_execute_sql 与原始生成 SQL 不一致

设计原则：
- fail-open（解析失败时返回 None 而非抛异常）：让上层根据 None 自行选择 fail-closed 策略
- dialect 默认 None（auto-detect），允许调用方显式传入
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from common.utils.utils import SQLBotLogUtil


@dataclass(frozen=True)
class SQLTableRef:
    name: str
    schema: Optional[str] = None
    catalog: Optional[str] = None

    @property
    def qualified_name(self) -> str:
        return f"{self.schema}.{self.name}" if self.schema else self.name


def _identifier_text(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def extract_table_names(sql: str, dialect: Optional[str] = None) -> Optional[set[str]]:
    """
    从 SQL 中提取所有真实物理表名（不含 CTE 别名 / 子查询别名）。

    返回:
        - set[str]：成功解析时返回去重后的表名集合
        - None：解析失败（让调用方按 fail-closed 策略处理）

    例子:
        extract_table_names("SELECT * FROM orders o JOIN region r ON o.rid=r.id")
        → {"orders", "region"}

        extract_table_names("WITH t AS (SELECT * FROM orders) SELECT * FROM t")
        → {"orders"}  （t 是 CTE 别名，不计入）
    """
    refs = extract_table_refs(sql, dialect=dialect)
    if refs is None:
        return None
    return {ref.name for ref in refs}


_SQLGLOT_DIALECTS = {
    'mysql': 'mysql', 'mariadb': 'mysql', 'doris': 'doris', 'starrocks': 'starrocks',
    'sqlserver': 'tsql', 'tsql': 'tsql',
    'pg': 'postgres', 'postgresql': 'postgres', 'postgres': 'postgres',
    'kingbase': 'postgres', 'opengauss': 'postgres', 'excel': 'postgres',
    'ck': 'clickhouse', 'clickhouse': 'clickhouse',
    'oracle': 'oracle', 'dm': 'oracle', 'redshift': 'redshift',
}

# Functions that read outside the catalogued tables (remote servers, files, large objects).
_BLOCKED_FUNCTION_PREFIXES = ('openquery', 'openrowset', 'opendatasource', 'dblink', 'pg_read_', 'pg_ls_dir',
                              'pg_stat_file', 'lo_')


def sqlglot_dialect(ds_type: Optional[str]) -> Optional[str]:
    """SQLGlot dialect for a datasource type (Data platform or SQLBot), or None for the generic dialect.
    Never returns a non-SQLGlot name such as 'dm' or 'es', which SQLGlot would reject as an unknown dialect."""
    return _SQLGLOT_DIALECTS.get(str(ds_type or '').strip().lower())


def _function_name(node: exp.Func) -> str:
    return str(node.name if isinstance(node, exp.Anonymous) else node.sql_name() or '').lower()


def extract_table_refs(sql: str, dialect: Optional[str] = None) -> Optional[set[SQLTableRef]]:
    """
    从 SQL 中提取真实物理表引用，保留 schema.table 信息。
    表来源不是普通表名（表值函数、OPENQUERY、超过三段的名称）或调用了读外部数据的函数时返回 None（拒绝），
    不能当作“不涉及任何表”放行。
    """
    if not sql or not sql.strip():
        return set()
    dialect = sqlglot_dialect(dialect)
    try:
        parsed_list = sqlglot.parse(sql, dialect=dialect)
    except sqlglot.errors.ParseError as exc:
        SQLBotLogUtil.warning(f"SQL parse failed (dialect={dialect}): {exc}")
        return None
    except Exception as exc:
        # 未知 dialect（如 dm）等非语法错误：退回通用 dialect 再试一次
        SQLBotLogUtil.warning(f"SQL parse with dialect={dialect} failed ({exc}); retry with generic dialect")
        try:
            parsed_list = sqlglot.parse(sql, dialect=None)
        except Exception as exc2:
            SQLBotLogUtil.warning(f"SQL parse fallback also failed: {exc2}")
            return None

    table_refs: set[SQLTableRef] = set()
    for stmt in parsed_list:
        if stmt is None:
            continue
        blocked = [name for name in map(_function_name, stmt.find_all(exp.Func))
                   if name.startswith(_BLOCKED_FUNCTION_PREFIXES)]
        if blocked:
            SQLBotLogUtil.warning(f"SQL reads outside catalogued tables via {sorted(set(blocked))}; deny")
            return None
        try:
            scopes = list(traverse_scope(stmt))
            tables = [source for scope in scopes for _, source in scope.selected_sources.values()
                      if isinstance(source, exp.Table)] if scopes else list(stmt.find_all(exp.Table))
        except Exception:
            return None
        for table in tables:
            name = table.name
            if not isinstance(table.this, exp.Identifier) or not name:
                # Table-valued function, OPENQUERY, or a name with more than three parts: not a catalogued table.
                SQLBotLogUtil.warning(f"SQL table source is not a plain table name: {table.sql()[:80]}; deny")
                return None
            table_refs.add(SQLTableRef(
                name=str(name),
                schema=_identifier_text(table.db),
                catalog=_identifier_text(table.catalog),
            ))

    return table_refs


def merge_tables(*sources: Iterable[str] | None) -> set[str]:
    """
    合并多个表名来源（如 LLM 返回的 tables + parser 解析的 tables），去重并归一化。
    """
    merged: set[str] = set()
    for src in sources:
        if not src:
            continue
        for name in src:
            if name:
                merged.add(str(name))
    return merged
