"""Pure configuration tests: do not access Docker or modify deployed files."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('install_nginx', Path(__file__).with_name('install-nginx.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
LOCATIONS = Path(__file__).with_name('nginx.locations.conf').read_text()


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.other = 'server {\n    listen 20270;\n    location / { proxy_pass http://other; }\n}\n'
        self.owned = 'server {\n    listen 20170;\n    location / { root /owned; }\n}\n'

    def test_only_owned_server_changes_and_repeat_is_noop(self):
        original = self.other + self.owned
        updated = installer.add_routes(original, LOCATIONS)
        self.assertTrue(updated.startswith(self.other))
        self.assertIn('location / { root /owned; }', updated)
        self.assertIn('/sqlbot-app/', updated)
        self.assertEqual(installer.add_routes(updated, LOCATIONS), updated)

    def test_partial_routes_are_not_overwritten(self):
        partial = self.owned.replace('root /owned;', 'root /sqlbot-app/;')
        with self.assertRaisesRegex(RuntimeError, 'Partial'):
            installer.add_routes(partial, LOCATIONS)

    def test_missing_owned_server_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            installer.add_routes(self.other, LOCATIONS)

    def test_duplicate_owned_server_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'exactly one'):
            installer.add_routes(self.owned + self.owned, LOCATIONS)


if __name__ == '__main__':
    unittest.main()
