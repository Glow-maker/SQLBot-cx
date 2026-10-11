import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import sqlbot_xpack  # Match main.py initialization order for the bundled extension.
from fastapi import FastAPI
from apps.system.middleware import auth


class TenantMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_platform_requests_require_matching_tenant(self):
        app = FastAPI()
        app.add_middleware(auth.TokenMiddleware)
        reached = []

        @app.get('/private-test')
        async def private():
            reached.append(True)
            return {'ok': True}

        with patch.object(auth.settings, 'PLATFORM_AUTH_ENABLED', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_STRICT_MODE', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_REQUIRE_TENANT_ID_HEADER', True), \
             patch.object(auth.TokenMiddleware, 'validateToken', AsyncMock(return_value=(True, SimpleNamespace(oid=124)))), \
             patch.object(auth.TokenMiddleware, '_inject_platform_assistant_if_enabled'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for tenant in [None, '999', 'invalid']:
                    headers = {'Authorization': 'Bearer test'}
                    if tenant is not None:
                        headers['tenant-id'] = tenant
                    self.assertEqual((await client.get('/private-test', headers=headers)).status_code, 403)
                self.assertEqual(reached, [])
                self.assertEqual((await client.get('/private-test', headers={'Authorization': 'Bearer test', 'tenant-id': '124'})).status_code, 200)
                self.assertEqual(len(reached), 1)
