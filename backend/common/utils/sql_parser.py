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

import re
from dataclasses import dataclass
from typing import Iterable, Optional

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialects
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
    # Doris/StarRocks parse with the MySQL dialect: their own dialects reject GROUP_CONCAT ... SEPARATOR.
    'mysql': 'mysql', 'mariadb': 'mysql', 'doris': 'mysql', 'starrocks': 'mysql',
    'sqlserver': 'tsql', 'tsql': 'tsql',
    'pg': 'postgres', 'postgresql': 'postgres', 'postgres': 'postgres',
    'kingbase': 'postgres', 'opengauss': 'postgres', 'excel': 'postgres',
    'ck': 'clickhouse', 'clickhouse': 'clickhouse',
    'oracle': 'oracle', 'dm': 'oracle', 'redshift': 'redshift',
}
_KNOWN_SQLGLOT_DIALECTS = frozenset(d.value for d in Dialects if d.value)

# Functions that read outside the catalogued tables: remote servers, files, large objects, or SQL given as a string.
# Matched against the lower-cased name and every dotted suffix of it (pg_catalog.query_to_xml, SYS.DBMS_XMLGEN.GETXML).
_BLOCKED_FUNCTION_PREFIXES = (
    'openquery', 'openrowset', 'opendatasource', 'dblink', 'pg_read_', 'pg_ls_', 'pg_stat_file', 'pg_current_logfile',
    'lo_', 'query_to_xml', 'table_to_xml', 'schema_to_xml', 'database_to_xml', 'cursor_to_xml', 'ts_stat',
    'load_file', 'bfilename', 'dbms_xmlgen.', 'dbms_xmlquery.', 'dbms_lob.', 'utl_')

# Built-in set-returning functions allowed as a FROM / LATERAL / APPLY source, unqualified or under pg_catalog / sys.
_ALLOWED_SOURCE_FUNCTIONS = frozenset((
    'generate_series', 'exploding_generate_series', 'unnest', 'json_each', 'json_each_text', 'jsonb_each', 'jsonb_each_text',
    'json_array_elements', 'json_array_elements_text', 'jsonb_array_elements', 'jsonb_array_elements_text',
    'json_to_recordset', 'jsonb_to_recordset', 'regexp_split_to_table', 'string_to_table',
    'json_table', 'string_split', 'openjson', 'numbers', 'table'))
_ALLOWED_SOURCE_SCHEMAS = frozenset(('', 'pg_catalog', 'sys'))
# sqlglot's sql_name() spells some classes oddly (J_S_O_N_TABLE, OPEN_J_S_O_N); compare without underscores.
_ALLOWED_SOURCE_KEYS = frozenset(name.replace('_', '') for name in _ALLOWED_SOURCE_FUNCTIONS)

# String concatenation: || (DPipe), CONCAT, CONCAT_WS, and + (T-SQL).
_CONCAT_TYPES = (exp.DPipe, exp.Concat, exp.ConcatWs, exp.Add)

SQL_PARSE_FAILED = 'sql_parse_failed'
UNSUPPORTED_TABLE_SOURCE = 'unsupported_table_source'


def sqlglot_dialect(ds_type: Optional[str]) -> Optional[str]:
    """SQLGlot dialect for a datasource type (Data platform or SQLBot), or None for the generic dialect.
    Names SQLGlot itself knows pass through; never returns a non-SQLGlot name such as 'dm' or 'es'."""
    key = str(ds_type or '').strip().lower()
    return _SQLGLOT_DIALECTS.get(key) or (key if key in _KNOWN_SQLGLOT_DIALECTS else None)


def _plain(node) -> str:
    """Identifier / column / dotted name without quotes or brackets, lower-cased."""
    return re.sub(r'["`\[\]]', '', node.sql()).lower() if node is not None else ''


def _base_name(node: exp.Func) -> str:
    return str(node.name if isinstance(node, exp.Anonymous) else node.sql_name() or '').lower()


def _function_name(node: exp.Func) -> str:
    """Lower-cased function name, qualified when written as package.function."""
    parent = node.parent
    if isinstance(parent, exp.Dot) and parent.expression is node:
        return _plain(parent.this) + '.' + _base_name(node)
    return _base_name(node)


def _blocked_name(name: str) -> bool:
    parts = name.split('.')
    return any('.'.join(parts[i:]).startswith(_BLOCKED_FUNCTION_PREFIXES) for i in range(len(parts)))


def _concat_text(node) -> str:
    """Text a string concatenation builds; any part that is not a literal stands in as the identifier x."""
    if isinstance(node, exp.Literal):
        return str(node.this)
    if isinstance(node, exp.Paren):
        return _concat_text(node.this)
    if isinstance(node, (exp.DPipe, exp.Add)):
        return _concat_text(node.this) + _concat_text(node.expression)
    if isinstance(node, exp.ConcatWs) and node.expressions:
        separator, *parts = node.expressions
        return _concat_text(separator).join(map(_concat_text, parts))
    if isinstance(node, exp.Concat):
        return ''.join(map(_concat_text, node.expressions))
    return 'x'


def _in_call(node) -> bool:
    """Inside the arguments of a function other than a concatenation."""
    parent = node.parent
    while parent is not None:
        if isinstance(parent, exp.Func) and not isinstance(parent, _CONCAT_TYPES):
            return True
        parent = parent.parent
    return False


def _runs_sql_text(stmt: exp.Expression, dialect: Optional[str]) -> bool:
    """A string argument of a function that is itself a query (query_to_xml('select ...'), EXECUTE-like UDFs),
    also when the query is built by concatenating literals ('select * from ' || 'hr.salary')."""
    texts = [literal.this for literal in stmt.find_all(exp.Literal) if literal.is_string and literal.find_ancestor(exp.Func)]
    texts += [_concat_text(node) for node in stmt.find_all(*_CONCAT_TYPES)
              if not isinstance(node.parent, _CONCAT_TYPES) and _in_call(node)]
    for text in texts:
        if not text or not re.search(r'\b(select|with)\b', text, re.I):
            continue
        try:
            parsed = sqlglot.parse_one(text, read=dialect)
            if isinstance(parsed, exp.Query) and parsed.find(exp.Table) is not None:
                return True
        except Exception:
            continue
    return False


def _allowed_source_function(func: exp.Func, qualifier: str) -> bool:
    name = _base_name(func).replace('_', '')
    if name not in _ALLOWED_SOURCE_KEYS or qualifier not in _ALLOWED_SOURCE_SCHEMAS:
        return False
    if name != 'table':
        return True
    # TABLE(expr) returns whatever expr yields as rows: a collection is fine, a query or a user function is not.
    return func.find(exp.Query) is None and not any(
        isinstance(inner, exp.Anonymous) or isinstance(inner.parent, exp.Dot)
        for inner in func.find_all(exp.Func) if inner is not func)


def _source_issue(stmt: exp.Expression, dialect: Optional[str]) -> Optional[str]:
    """Why a statement cannot be vouched for by its table references alone, or None."""
    blocked = sorted({name for name in map(_function_name, stmt.find_all(exp.Func)) if _blocked_name(name)})
    if blocked:
        return f"reads outside catalogued tables via {blocked}"
    if _runs_sql_text(stmt, dialect):
        return "passes SQL text to a function"
    for lateral in stmt.find_all(exp.Lateral):  # CROSS/OUTER APPLY, LATERAL
        target = lateral.this
        if isinstance(target, exp.Subquery):
            continue  # its tables are collected through the scopes
        if isinstance(target, exp.Dot) and isinstance(target.expression, exp.Func):
            func, qualifier = target.expression, _plain(target.this)
        elif isinstance(target, exp.Func):
            func, qualifier = target, ''
        else:
            return f"unsupported LATERAL/APPLY source {target.sql()[:60]}"
        if not _allowed_source_function(func, qualifier):
            return f"LATERAL/APPLY function {qualifier + '.' if qualifier else ''}{_base_name(func)}"
    return None


def inspect_table_refs(sql: str, dialect: Optional[str] = None) -> tuple[Optional[set[SQLTableRef]], Optional[str]]:
    """
    从 SQL 中提取真实物理表引用，保留 schema.table 信息；返回 (表引用, 拒绝原因)。
    拒绝原因：sql_parse_failed（解析不了）；unsupported_table_source（表来源不是普通表名：表值函数、
    OPENQUERY、db link、超过三段的名称、名单外的 APPLY/LATERAL 函数，或调用了读外部数据、执行 SQL 字符串的函数）。
    名单内的内置集合函数（generate_series、unnest、json(b)_each 等）不算物理表，参数里的子查询照常收集。
    """
    if not sql or not sql.strip():
        return set(), None
    dialect = sqlglot_dialect(dialect)
    try:
        parsed_list = sqlglot.parse(sql, dialect=dialect)
    except sqlglot.errors.ParseError as exc:
        SQLBotLogUtil.warning(f"SQL parse failed (dialect={dialect}): {exc}")
        return None, SQL_PARSE_FAILED
    except Exception as exc:
        # 非语法错误：退回通用 dialect 再试一次
        SQLBotLogUtil.warning(f"SQL parse with dialect={dialect} failed ({exc}); retry with generic dialect")
        try:
            parsed_list = sqlglot.parse(sql, dialect=None)
        except Exception as exc2:
            SQLBotLogUtil.warning(f"SQL parse fallback also failed: {exc2}")
            return None, SQL_PARSE_FAILED

    table_refs: set[SQLTableRef] = set()
    for stmt in parsed_list:
        if stmt is None:
            continue
        issue = _source_issue(stmt, dialect)
        if issue:
            SQLBotLogUtil.warning(f"SQL {issue}; deny")
            return None, UNSUPPORTED_TABLE_SOURCE
        try:
            scopes = list(traverse_scope(stmt))
            tables = [source for scope in scopes for _, source in scope.selected_sources.values()
                      if isinstance(source, exp.Table)] if scopes else list(stmt.find_all(exp.Table))
        except Exception:
            return None, SQL_PARSE_FAILED
        for table in tables:
            this = table.this
            if isinstance(this, exp.Func) and _allowed_source_function(this, _plain(table.args.get('db'))):
                continue  # built-in set-returning function, not a catalogued table
            alias = table.args.get('alias')
            db_link = (alias is not None and alias.this is not None and not isinstance(alias.this, exp.Identifier)) \
                or '@' in str(table.name)
            if not isinstance(this, exp.Identifier) or not table.name or db_link:
                # Table-valued function, OPENQUERY, db link, or a name with more than three parts.
                SQLBotLogUtil.warning(f"SQL table source is not a catalogued table: {table.sql()[:80]}; deny")
                return None, UNSUPPORTED_TABLE_SOURCE
            table_refs.add(SQLTableRef(
                name=str(table.name),
                schema=_identifier_text(table.db),
                catalog=_identifier_text(table.catalog),
            ))

    return table_refs, None


def extract_table_refs(sql: str, dialect: Optional[str] = None) -> Optional[set[SQLTableRef]]:
    """表引用；任何拒绝原因都返回 None（见 inspect_table_refs），不能当作“不涉及任何表”放行。"""
    return inspect_table_refs(sql, dialect)[0]


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
