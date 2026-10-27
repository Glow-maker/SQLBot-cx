import unittest
from unittest.mock import patch

from common.utils.sql_parser import SQLTableRef, extract_table_refs, inspect_table_refs, sqlglot_dialect


class SQLParserTests(unittest.TestCase):
    def test_platform_pg_parses_postgres_json_operator_without_fallback(self):
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning') as warning:
            refs = extract_table_refs(
                '''SELECT payload #>> '{customer,name}' FROM "audit"."orders"''',
                dialect='pg',
            )
        self.assertEqual(refs, {SQLTableRef(name='orders', schema='audit')})
        warning.assert_not_called()

    def test_pg_cte_keeps_physical_schemas_and_excludes_alias(self):
        sql = '''WITH recent AS (
            SELECT id FROM "sales"."orders" WHERE created_at > NOW() - INTERVAL '1 day'
        ) SELECT * FROM recent JOIN "archive"."orders" old USING (id)'''
        for dialect in ('pg', 'postgres'):
            with self.subTest(dialect=dialect):
                self.assertEqual(extract_table_refs(sql, dialect), {
                    SQLTableRef(name='orders', schema='sales'),
                    SQLTableRef(name='orders', schema='archive'),
                })

    def test_platform_type_strings_map_to_sqlglot_dialects(self):
        # Data sends lowercase type strings; SQL Server SQL uses TOP and brackets.
        for dialect in ('sqlserver', 'sqlServer'):
            with self.subTest(dialect=dialect):
                self.assertEqual(extract_table_refs('SELECT TOP 10 [a] FROM [dbo].[fee]', dialect),
                                 {SQLTableRef(name='fee', schema='dbo')})
        self.assertEqual(extract_table_refs('SELECT * FROM "audit"."orders"', 'postgresql'),
                         {SQLTableRef(name='orders', schema='audit')})

    def test_sqlglot_dialect_never_returns_an_unknown_dialect_name(self):
        self.assertEqual({t: sqlglot_dialect(t) for t in ('mysql', 'Doris', 'starrocks', 'sqlserver', 'sqlServer', 'pg',
                                                          'postgresql', 'kingbase', 'opengauss', 'excel', 'ck', 'oracle',
                                                          'dm', 'redshift', 'hive', 'es', None, '')},
                         {'mysql': 'mysql', 'Doris': 'mysql', 'starrocks': 'mysql', 'sqlserver': 'tsql',
                          'sqlServer': 'tsql', 'pg': 'postgres', 'postgresql': 'postgres', 'kingbase': 'postgres',
                          'opengauss': 'postgres', 'excel': 'postgres', 'ck': 'clickhouse', 'oracle': 'oracle',
                          'dm': 'oracle', 'redshift': 'redshift', 'hive': 'hive', 'es': None, None: None, '': None})
        # Doris keeps the MySQL dialect: its own sqlglot dialect rejects GROUP_CONCAT ... SEPARATOR.
        self.assertEqual(extract_table_refs("SELECT GROUP_CONCAT(a SEPARATOR ',') FROM biz.orders", 'doris'),
                         {SQLTableRef(name='orders', schema='biz')})

    def test_table_functions_and_remote_reads_fail_closed(self):
        # Gates treat an empty set as "no tables"; these must deny (None) instead.
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning'):
            for sql, dialect in (("SELECT TOP 10 [x].[a] FROM OPENQUERY([LNK], 'SELECT a FROM secret') [x]", 'sqlserver'),
                                 ('SELECT TOP 10 [t].[a] FROM [dbo].[fn_secret](1) [t]', 'sqlserver'),
                                 ("SELECT [f].[a] FROM [dbo].[fee] [f] CROSS JOIN OPENQUERY([LNK], 'select 1') [o]", 'sqlserver'),
                                 ("SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x;', 'select 1') AS r", 'sqlserver'),
                                 ('SELECT * FROM [HIS].[dbo].[secret].[fee]', 'sqlserver'),
                                 ("SELECT * FROM dblink('host=x', 'select 1') AS t(a int)", 'pg'),
                                 ("SELECT pg_read_file('/etc/passwd')", 'pg'),
                                 ("SELECT lo_get(1)", 'pg')):
                with self.subTest(sql=sql):
                    self.assertIsNone(extract_table_refs(sql, dialect))
        # Ordinary functions and queries still parse.
        self.assertEqual(extract_table_refs('SELECT COUNT(*), MAX("amount") FROM "sales"."orders"', 'pg'),
                         {SQLTableRef(name='orders', schema='sales')})
        self.assertEqual(extract_table_refs('SELECT 1', 'pg'), set())
        self.assertEqual(extract_table_refs('SELECT `a` FROM `bayuan`.`orders` LIMIT 10', 'mysql'),
                         {SQLTableRef(name='orders', schema='bayuan')})
        for sql in ('SELECT * FROM "DMGT"."T" LIMIT 10', 'SELECT TOP 10 * FROM "DMGT"."T"'):
            with self.subTest(sql=sql):
                self.assertEqual(extract_table_refs(sql, 'dm'), {SQLTableRef(name='T', schema='DMGT')})

    def test_sql_text_functions_apply_lateral_and_db_links_are_unsupported_sources(self):
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning'):
            for sql, dialect in (
                    ("SELECT query_to_xml('select * from hr.salary', true, false, '')", 'pg'),
                    ("SELECT o.id, query_to_xml('select * from hr.salary', true, false, '') FROM sales.orders o", 'pg'),
                    ("SELECT table_to_xml('hr.salary', true, false, '')", 'pg'),
                    ("SELECT ts_stat('select v from t')", 'pg'),
                    ("SELECT DBMS_XMLGEN.GETXML('select * from HR.SALARY') AS \"x\" FROM \"SALES\".\"ORDERS\" \"o\"", 'dm'),
                    ("SELECT LOAD_FILE('/etc/passwd')", 'mysql'),
                    ("SELECT my_exec('select * from hr.salary') FROM sales.orders", 'pg'),  # any function given SQL text
                    ("SELECT TOP 10 [f].[a] FROM [dbo].[fee] [f] CROSS APPLY [dbo].[fn_secret]([f].[id]) [s]", 'sqlserver'),
                    ("SELECT TOP 10 [f].[a] FROM [dbo].[fee] [f] OUTER APPLY [dbo].[fn_secret]([f].[id]) [s]", 'sqlserver'),
                    ("SELECT * FROM [dbo].[fee] f CROSS APPLY sys.dm_exec_sql_text(f.h)", 'sqlserver'),
                    ('SELECT o.id, s.v FROM "sales"."orders" o CROSS JOIN LATERAL "hr"."fn_secret"(o.id) AS s(v)', 'pg'),
                    ('SELECT COUNT(*) AS "c" FROM "HR"."SALARY"@"PROD"', 'dm'),
                    ('SELECT * FROM S.T@LINK', 'oracle'),
                    ('SELECT * FROM hr.generate_series(1, 3) g', 'pg'),
                    # Schema-qualified blocked names, and SQL text built by concatenation.
                    ("SELECT pg_catalog.query_to_xml('select * from ' || 'hr.salary', true, false, '')", 'pg'),
                    ("SELECT pg_catalog.query_to_xml('select * from ' || 'hr.salary', true, false, '') FROM sales.orders o", 'pg'),
                    ("SELECT SYS.DBMS_XMLGEN.GETXML('select * from ' || 'HR.SALARY') FROM \"SALES\".\"ORDERS\"", 'dm'),
                    ("SELECT pg_catalog.pg_read_file('/etc/passwd')", 'pg'),
                    ("SELECT my_exec('select * from ' || 'hr.salary') FROM sales.orders", 'pg'),
                    ("SELECT my_exec('select * from hr.' || o.name) FROM sales.orders o", 'pg'),
                    ("SELECT my_exec(CONCAT('select * from ', 'hr.salary')) FROM `biz`.`orders`", 'mysql'),
                    ("SELECT my_exec(CONCAT_WS(' ', 'select * from', 'hr.salary')) FROM `biz`.`orders`", 'mysql'),
                    ("SELECT dbo.run_sql('select * from ' + 'hr.salary')", 'sqlserver'),
                    # TABLE() over a user function is a table function.
                    ('SELECT t.COLUMN_VALUE FROM TABLE(hr.fn_secret(1)) t', 'oracle'),
                    ('SELECT t.COLUMN_VALUE FROM TABLE(fn_secret(1)) t', 'oracle')):
                with self.subTest(sql=sql):
                    self.assertEqual(inspect_table_refs(sql, dialect), (None, 'unsupported_table_source'))
            self.assertEqual(inspect_table_refs('SELECT * FROM (', 'pg'), (None, 'sql_parse_failed'))

    def test_builtin_set_returning_functions_are_not_tables(self):
        orders = {SQLTableRef(name='orders', schema='sales')}
        for sql, dialect, expected in (
                ("SELECT d::date, count(o.id) FROM generate_series('2026-01-01'::date, '2026-01-31'::date, interval '1 day') AS d "
                 "LEFT JOIN sales.orders o ON o.day = d GROUP BY d", 'pg', orders),
                ('SELECT g.d FROM generate_series(1, 12) AS g(d)', 'pg', set()),
                ('SELECT g FROM pg_catalog.generate_series(1, 3) AS g', 'pg', set()),
                ('SELECT o.id, g.n FROM sales.orders o CROSS JOIN generate_series(1, 3) AS g(n)', 'pg', orders),
                ('SELECT e FROM sales.orders o, jsonb_array_elements(o.items) AS e', 'pg', orders),
                ('SELECT k, v FROM sales.orders o, json_each(o.attrs) AS j(k, v)', 'pg', orders),
                ("SELECT p FROM sales.orders o, regexp_split_to_table(o.tags, ',') AS p", 'pg', orders),
                ('SELECT o.id, e.value FROM sales.orders o CROSS JOIN LATERAL jsonb_array_elements(o.items) AS e(value)', 'pg', orders),
                ('SELECT u FROM sales.orders o, unnest(o.arr) AS u', 'pg', orders),
                # A subquery inside the function arguments is still collected.
                ('SELECT n FROM generate_series(1, (SELECT count(*) FROM hr.salary)) AS n', 'pg', {SQLTableRef(name='salary', schema='hr')}),
                ("SELECT jt.a FROM `biz`.`orders` o, JSON_TABLE(o.doc, '$[*]' COLUMNS (a INT PATH '$.a')) AS jt", 'mysql',
                 {SQLTableRef(name='orders', schema='biz')}),
                ("SELECT [f].[id], [s].[value] FROM [dbo].[fee] [f] CROSS APPLY STRING_SPLIT([f].[tags], ',') [s]", 'sqlserver',
                 {SQLTableRef(name='fee', schema='dbo')}),
                ('SELECT [f].[id], [j].[k] FROM [dbo].[fee] [f] CROSS APPLY OPENJSON([f].[doc]) [j]', 'sqlserver',
                 {SQLTableRef(name='fee', schema='dbo')}),
                ('SELECT number FROM numbers(10)', 'ck', set()),
                ('SELECT t.COLUMN_VALUE FROM "S"."O" o, TABLE(o.tags) t', 'oracle', {SQLTableRef(name='O', schema='S')}),
                ("SELECT CONCAT('select', a) FROM sales.orders", 'pg', orders),  # a word, not a query
                ("SELECT upper('select ' || o.name) FROM sales.orders o", 'pg', orders),  # builds no query over a table
                ("SELECT o.a || ' / ' || o.b FROM sales.orders o", 'pg', orders),
                ('SELECT t.COLUMN_VALUE FROM "S"."O" o, TABLE(CAST(o.tags AS t_list)) t', 'oracle', {SQLTableRef(name='O', schema='S')})):
            with self.subTest(sql=sql):
                self.assertEqual(extract_table_refs(sql, dialect), expected)

    def test_pg_invalid_sql_returns_none_for_permission_gate(self):
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning'):
            self.assertIsNone(extract_table_refs('SELECT * FROM (', dialect='pg'))

    def test_unknown_dialect_still_uses_generic_fallback(self):
        # Unknown types map to the generic dialect up front instead of failing once and retrying.
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning') as warning:
            refs = extract_table_refs('SELECT * FROM sales.orders', dialect='unknown_vendor')
        self.assertEqual(refs, {SQLTableRef(name='orders', schema='sales')})
        warning.assert_not_called()


if __name__ == '__main__':
    unittest.main()
