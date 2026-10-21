import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import sqlbot_xpack  # Match main.py initialization order for the bundled extension.
from fastapi import FastAPI, Request
from apps.system.middleware import auth


class TenantMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_strict_platform_requests_use_verified_token_tenant_not_request_header(self):
        app = FastAPI()
        app.add_middleware(auth.TokenMiddleware)
        reached = []

        @app.get('/private-test')
        async def private(request: Request):
            tenant = request.state.current_user.oid
            reached.append(tenant)
            return {'tenant': tenant}

        async def validate(token, _trans, _path):
            tenant = {'Bearer tenant-one': 124, 'Bearer tenant-two': 125}[token]
            return True, SimpleNamespace(oid=tenant, auth_source='platform')

        with patch.object(auth.settings, 'PLATFORM_AUTH_ENABLED', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_STRICT_MODE', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_REQUIRE_TENANT_ID_HEADER', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER', True), \
             patch.object(auth.TokenMiddleware, 'validateToken', AsyncMock(side_effect=validate)), \
             patch.object(auth.TokenMiddleware, '_inject_platform_assistant_if_enabled'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                for token, verified_tenant in [('tenant-one', 124), ('tenant-two', 125)]:
                    for tenant in [None, '', '999', 'invalid', '124', '125']:
                        with self.subTest(token=token, tenant=tenant):
                            headers = {'Authorization': f'Bearer {token}'}
                            if tenant is not None:
                                headers['tenant-id'] = tenant
                            response = await client.get('/private-test', headers=headers)
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.json(), {'tenant': verified_tenant})
                self.assertEqual(reached, [124] * 6 + [125] * 6)

    async def test_invalid_platform_token_remains_unauthorized(self):
        app = FastAPI()
        app.add_middleware(auth.TokenMiddleware)
        reached = []

        @app.get('/private-test')
        async def private():
            reached.append(True)
            return {'ok': True}

        with patch.object(auth.TokenMiddleware, 'validateToken', AsyncMock(return_value=(False, 'Invalid token'))), \
             patch.object(auth.TokenMiddleware, '_inject_platform_assistant_if_enabled') as inject:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                response = await client.get('/private-test', headers={'Authorization': 'Bearer expired', 'tenant-id': '124'})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(reached, [])
        inject.assert_not_called()
