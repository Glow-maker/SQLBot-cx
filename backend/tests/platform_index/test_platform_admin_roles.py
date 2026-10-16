"""Administrator authority comes from verified System roles, never a browser flag."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx
import sqlbot_xpack  # Match the bundled extension's application initialization order.
from fastapi import FastAPI, HTTPException
from sqlmodel import Session, create_engine

from apps.operations import knowledge
from apps.operations.models import OperationsGrant
from apps.operations.reports import _user_snapshot
from apps.operations.security import is_instance_admin, require_operator
from apps.system.middleware import auth


class PlatformAdminRolesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.middleware = auth.TokenMiddleware(FastAPI())
        self.identity = auth.PlatformTokenIdentity(
            user_id=31, account='operator', tenant_id=124, nickname='Operator', payload={})
        self.requests = []
        self.body = {'code': 0, 'data': {'user': {'id': 31}, 'roles': ['tenant_admin']}}
        self.status = 200
        self.transport_error = None
        self.clock = SimpleNamespace(monotonic=Mock(return_value=100.0))
        self.enterContext(patch.object(auth, 'time', self.clock))
        for key, value in {
            'PLATFORM_AUTH_BASE_URL': 'http://system.invalid',
            'PLATFORM_AUTH_HTTP_TIMEOUT_SECONDS': 10.0,
            'PLATFORM_AUTH_TENANT_ID_HEADER': 'tenant-id',
        }.items():
            self.enterContext(patch.object(auth.settings, key, value))
        client_class = httpx.AsyncClient

        def handle(request):
            self.requests.append(request)
            if self.transport_error:
                raise self.transport_error
            return httpx.Response(self.status, json=self.body)

        def client(**kwargs):
            return client_class(transport=httpx.MockTransport(handle), **kwargs)

        self.enterContext(patch.object(auth.httpx, 'AsyncClient', side_effect=client))

    async def test_enabled_system_admin_roles_grant_all_backend_access(self):
        for role in ('super_admin', 'tenant_admin'):
            self.middleware._platform_admin_cache.clear()
            self.body['data']['roles'] = [role]
            verified = await self.middleware._resolve_platform_admin('test-token', self.identity)
            user = self.middleware._build_user_from_platform_token(self.identity, is_admin=verified)
            self.assertTrue(user.isAdmin)
            self.assertEqual(user.weight, 1)
            self.assertTrue(user.platform_admin_verified)
            self.assertEqual(user.auth_source, 'platform')
            self.assertTrue(is_instance_admin(user))
            self.assertEqual(require_operator(None, user, 125, 'platform'), ('platform', 125))
            self.assertEqual(require_operator(None, user, 1, 'local'), ('local', 1))
            self.assertTrue(is_instance_admin(SimpleNamespace(**_user_snapshot(user))))
        self.assertEqual(str(self.requests[0].url),
                         'http://system.invalid/admin-api/system/auth/get-permission-info')
        self.assertEqual(self.requests[0].headers['Authorization'], 'Bearer test-token')
        self.assertEqual(self.requests[0].headers['tenant-id'], '124')

    async def test_account_name_user_id_and_browser_admin_flag_are_not_roles(self):
        self.identity.user_id, self.identity.account = 1, 'admin'
        self.body['data'] = {'user': {'id': 1}, 'roles': ['ordinary'], 'isAdmin': True}
        self.assertFalse(await self.middleware._resolve_platform_admin('test-token', self.identity))
        user = self.middleware._build_user_from_platform_token(self.identity)
        self.assertFalse(user.isAdmin)
        self.assertEqual(user.weight, 0)
        user.isAdmin = True
        self.assertFalse(is_instance_admin(user))

    async def test_invalid_user_roles_or_failed_lookup_never_grants_admin(self):
        bodies = [
            {'code': 0, 'data': {'user': {'id': 32}, 'roles': ['super_admin']}},
            {'code': 0, 'data': {'user': {'id': True}, 'roles': ['super_admin']}},
            {'code': 0, 'data': {'user': {'id': 31.5}, 'roles': ['super_admin']}},
            {'code': 0, 'data': {'user': {'id': 31}, 'roles': 'super_admin'}},
            {'code': 1, 'data': {'user': {'id': 31}, 'roles': ['super_admin']}},
            {'code': 0, 'data': None},
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.middleware._platform_admin_cache.clear()
                self.body = body
                self.assertFalse(await self.middleware._resolve_platform_admin('test-token', self.identity))
        self.middleware._platform_admin_cache.clear()
        self.status = 503
        self.assertFalse(await self.middleware._resolve_platform_admin('test-token', self.identity))
        self.middleware._platform_admin_cache.clear()
        self.transport_error = httpx.ReadTimeout('not a deployed endpoint')
        self.assertFalse(await self.middleware._resolve_platform_admin('test-token', self.identity))

    async def test_role_cache_is_token_and_tenant_scoped_and_expires_after_30_seconds(self):
        self.assertTrue(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.body['data']['roles'] = []
        self.assertTrue(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(await self.middleware._resolve_platform_admin('token-b', self.identity))
        self.identity.tenant_id = 125
        self.assertFalse(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.identity.tenant_id = 124
        self.clock.monotonic.return_value = 131.0
        self.assertFalse(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.assertEqual(len(self.requests), 4)
        self.assertNotIn('token-a', repr(self.middleware._platform_admin_cache))

    async def test_failed_lookup_is_cached_briefly_without_reusing_previous_authority(self):
        self.assertTrue(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.clock.monotonic.return_value = 131.0
        self.status = 503
        self.assertFalse(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.status = 200
        self.assertFalse(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.assertEqual(len(self.requests), 2)
        self.clock.monotonic.return_value = 137.0
        self.assertTrue(await self.middleware._resolve_platform_admin('token-a', self.identity))
        self.assertEqual(len(self.requests), 3)

    async def test_compatibility_mode_accepts_platform_admin_for_system_pages(self):
        with patch.object(auth.settings, 'PLATFORM_AUTH_ENABLED', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_STRICT_MODE', False), \
             patch.object(auth, 'resolve_platform_token_identity', AsyncMock(return_value=self.identity)), \
             patch.object(self.middleware, '_build_user_from_local_jwt', AsyncMock()) as local:
            valid, user = await self.middleware.validateToken('Bearer test-token', lambda value: value,
                                                             '/api/v1/system/ai_model')
            self.assertTrue(valid)
            self.assertTrue(user.isAdmin)
            local.assert_not_awaited()

    async def test_cached_admin_authority_does_not_bypass_token_authentication(self):
        self.assertTrue(await self.middleware._resolve_platform_admin('revoked-token', self.identity))
        with patch.object(auth.settings, 'PLATFORM_AUTH_ENABLED', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_STRICT_MODE', True), \
             patch.object(auth.settings, 'PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN', False), \
             patch.object(auth, 'resolve_platform_token_identity', AsyncMock(return_value=None)):
            valid, _ = await self.middleware.validateToken('Bearer revoked-token', lambda value: value,
                                                          '/api/v1/operations/access')
            self.assertFalse(valid)

    async def test_missing_auth_service_does_not_guess_admin_or_another_service(self):
        with patch.object(auth.settings, 'PLATFORM_AUTH_BASE_URL', None):
            self.assertFalse(await self.middleware._resolve_platform_admin('test-token', self.identity))
        self.assertEqual(self.requests, [])

    async def test_cross_tenant_operations_access_does_not_expand_data_refresh(self):
        user = self.middleware._build_user_from_platform_token(self.identity, is_admin=True)
        session = Mock()
        session.scalars.return_value.all.return_value = []
        self.assertFalse(knowledge.catalog(session, user, 'platform', 125)['can_refresh'])
        self.assertTrue(knowledge.catalog(session, user, 'platform', 124)['can_refresh'])
        with self.assertRaises(HTTPException) as denied:
            await knowledge.refresh_catalog(Mock(), session, user, 'platform', 125)
        self.assertEqual(denied.exception.status_code, 403)
        self.assertEqual(self.requests, [])


class PlatformAdminGrantCompatibilityTests(unittest.TestCase):
    def test_explicit_grant_remains_a_tenant_scoped_path_for_non_admins(self):
        engine = create_engine('sqlite:///:memory:')
        OperationsGrant.__table__.create(engine)
        user = SimpleNamespace(id=31, oid=124, auth_source='platform', isAdmin=False,
                               platform_admin_verified=False)
        try:
            with Session(engine) as session:
                session.add(OperationsGrant(tenant_id=124, user_id=31, updated_by=1))
                session.commit()
                self.assertEqual(require_operator(session, user, 124, 'platform'), ('platform', 124))
                with self.assertRaises(HTTPException):
                    require_operator(session, user, 125, 'platform')
                user.isAdmin = user.platform_admin_verified = True
                grant = session.get(OperationsGrant, 1)
                grant.enabled = False
                session.add(grant)
                session.commit()
                self.assertEqual(require_operator(session, user, 125, 'platform'), ('platform', 125))
        finally:
            engine.dispose()

    def test_local_admin_is_enabled_by_default_without_changing_whitelist(self):
        fields = type(auth.settings).model_fields
        self.assertTrue(fields['PLATFORM_AUTH_ALLOW_LOCAL_ADMIN_LOGIN'].default)
        self.assertEqual(fields['PLATFORM_AUTH_LOCAL_ADMIN_ACCOUNTS'].default, 'admin')


if __name__ == '__main__':
    unittest.main()
