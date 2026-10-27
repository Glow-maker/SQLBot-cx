import unittest
from unittest.mock import patch

from common.utils.sql_parser import SQLTableRef, extract_table_refs, sqlglot_dialect


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
                                                          'dm', 'redshift', 'es', None, '')},
                         {'mysql': 'mysql', 'Doris': 'doris', 'starrocks': 'starrocks', 'sqlserver': 'tsql',
                          'sqlServer': 'tsql', 'pg': 'postgres', 'postgresql': 'postgres', 'kingbase': 'postgres',
                          'opengauss': 'postgres', 'excel': 'postgres', 'ck': 'clickhouse', 'oracle': 'oracle',
                          'dm': 'oracle', 'redshift': 'redshift', 'es': None, None: None, '': None})

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
