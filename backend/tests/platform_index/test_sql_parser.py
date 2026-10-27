import unittest
from unittest.mock import patch

from common.utils.sql_parser import SQLTableRef, extract_table_refs


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

    def test_pg_invalid_sql_returns_none_for_permission_gate(self):
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning'):
            self.assertIsNone(extract_table_refs('SELECT * FROM (', dialect='pg'))

    def test_unknown_dialect_still_uses_generic_fallback(self):
        with patch('common.utils.sql_parser.SQLBotLogUtil.warning') as warning:
            refs = extract_table_refs('SELECT * FROM sales.orders', dialect='unknown_vendor')
        self.assertEqual(refs, {SQLTableRef(name='orders', schema='sales')})
        warning.assert_called_once()
        self.assertIn('retry with generic dialect', warning.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
