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

from typing import Iterable, Optional

import sqlglot
from sqlglot import exp

from common.utils.utils import SQLBotLogUtil


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
    if not sql or not sql.strip():
        return set()
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

    table_names: set[str] = set()
    cte_aliases: set[str] = set()

    for stmt in parsed_list:
        if stmt is None:
            continue
        # 收集 CTE 别名，从最终 tables 集合中排除
        for cte in stmt.find_all(exp.CTE):
            alias = cte.alias_or_name
            if alias:
                cte_aliases.add(str(alias).lower())

        for table in stmt.find_all(exp.Table):
            name = table.name
            if not name:
                continue
            if str(name).lower() in cte_aliases:
                continue
            table_names.add(str(name))

    return table_names


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
