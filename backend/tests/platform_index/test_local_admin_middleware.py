import json
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import sqlbot_xpack  # Match main.py initialization order for the bundled extension.
from fastapi import FastAPI, Request

from apps.system.middleware import auth


class LocalAdminMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.secret = 'middleware-test-secret-not-a-deployed-credential'
        for name, value in {
            'PLATFORM_AUTH_ENABLED': True,
            'PLATFORM_AUTH_STRICT_MODE': True,
            'PLATFORM_AUTH_REQUIRE_TENANT_ID_HEADER': True,
            'PLATFORM_AUTH_TENANT_ID_HEADER': 'tenant-id',
            'PLATFORM_AUTH_ACCEPT_AUTHORIZATION_HEADER': True,
            'PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN': True,
            'PLATFORM_AUTH_LOCAL_ADMIN_ACCOUNTS': 'admin',
            'PLATFORM_DATASOURCE_ENABLED': True,
            'PLATFORM_DATASOURCE_BASE_URL': 'http://data.invalid',
            'SECRET_KEY': self.secret,
        }.items():
            self.enterContext(patch.object(auth.settings, name, value))
        self.resolver = self.enterContext(patch.object(
            auth, 'resolve_platform_token_identity', AsyncMock(return_value=None)))
        self.enterContext(patch.object(auth.TokenMiddleware, '_resolve_platform_admin', AsyncMock(return_value=False)))
        self.enterContext(patch.object(auth, 'Session'))
        self.user_info = self.enterContext(patch.object(auth, 'get_user_info', AsyncMock(
            return_value={
                'id': 1, 'account': 'admin', 'oid': 1, 'name': 'Local administrator',
                'email': 'admin@local.test', 'status': 1, 'isAdmin': True,
            })))
        self.app = FastAPI()
        self.app.add_middleware(auth.TokenMiddleware)
        self.reached = []

        @self.app.get('/private-test')
        async def private(request: Request):
            self.reached.append(True)
            assistant = getattr(request.state, 'assistant', None)
            return {
                'auth_source': request.state.current_user.auth_source,
                'tenant': request.state.current_user.oid,
                'assistant': None if assistant is None else {
                    'name': assistant.name, 'certificate': json.loads(assistant.certificate),
                },
            }

    def local_token(self, secret=None):
        return jwt.encode(
            {'id': 1, 'account': 'admin', 'oid': 1, 'exp': time.time() + 60},
            secret or self.secret, algorithm=auth.security.ALGORITHM)

    async def request(self, token, tenant=None):
        headers = {'Authorization': f'Bearer {token}'}
        if tenant is not None:
            headers['tenant-id'] = tenant
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url='http://test'
        ) as client:
            return await client.get('/private-test', headers=headers)

    async def test_verified_local_admin_without_platform_tenant_has_no_data_assistant(self):
        response = await self.request(self.local_token())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'auth_source': 'local', 'tenant': 1, 'assistant': None})
        self.user_info.assert_awaited_once()

    async def test_platform_header_does_not_change_verified_local_admin_scope(self):
        response = await self.request(self.local_token(), '124')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {'auth_source': 'local', 'tenant': 1, 'assistant': None})

    async def test_verified_platform_token_controls_identity_and_data_certificate(self):
        for verified_tenant in (124, 125):
            self.resolver.return_value = auth.PlatformTokenIdentity(
                user_id=7, account='operator', tenant_id=verified_tenant, nickname='Operator', payload={})
            token = f'platform-test-token-{verified_tenant}'
            for tenant in (None, '', '1', 'invalid', '124', '125'):
                with self.subTest(verified_tenant=verified_tenant, tenant=tenant):
                    response = await self.request(token, tenant)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['auth_source'], 'platform')
                    self.assertEqual(response.json()['tenant'], verified_tenant)
                    certificate = response.json()['assistant']['certificate']
                    self.assertIn({'target': 'header', 'key': 'Authorization',
                                   'value': f'Bearer {token}'}, certificate)
                    self.assertIn({'target': 'header', 'key': 'tenant-id',
                                   'value': str(verified_tenant)}, certificate)
        self.user_info.assert_not_awaited()

    async def test_invalid_and_expired_platform_tokens_do_not_fall_back_to_local_admin(self):
        for token in ('invalid-platform-token', 'expired-platform-token'):
            with self.subTest(token=token):
                response = await self.request(token)
                self.assertEqual(response.status_code, 401)
        self.assertEqual(self.reached, [])
        self.user_info.assert_not_awaited()

    async def test_local_admin_exception_still_requires_enabled_whitelist(self):
        with patch.object(auth.settings, 'PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN', False):
            self.assertEqual((await self.request(self.local_token())).status_code, 401)
        self.user_info.return_value['account'] = 'ordinary-user'
        self.assertEqual((await self.request(self.local_token())).status_code, 401)
        self.assertEqual(self.reached, [])

    async def test_local_admin_exception_still_requires_valid_jwt_and_active_account(self):
        self.assertEqual((await self.request(self.local_token('incorrect-signing-key-for-test-only'))).status_code, 401)
        self.user_info.assert_not_awaited()
        self.user_info.return_value['status'] = 0
        self.assertEqual((await self.request(self.local_token())).status_code, 401)
        self.assertEqual(self.reached, [])
