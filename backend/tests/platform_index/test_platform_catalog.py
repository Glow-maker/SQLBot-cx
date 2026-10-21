import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI

from apps.datasource.api import platform_catalog as api
from apps.datasource.api import platform_embeddings as auth_api


class CatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.identity = SimpleNamespace(tenant_id=124, user_id=361)
        self.headers = {"Authorization": "Bearer current-user", "tenant-id": "124"}
        self.path = "/datasource/platform/question-starters"
        for name, value in (("PLATFORM_AUTH_ENABLED", True), ("PLATFORM_DATASOURCE_ENABLED", True),
                            ("PLATFORM_DATASOURCE_BASE_URL", "http://data.test")):
            patcher = patch.object(api.settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def request(self, body, status=200, headers=None):
        headers = self.headers if headers is None else headers
        def handle(request):
            self.assertEqual(str(request.url), "http://data.test/openapi/sqlbot/datasources/query")
            self.assertEqual(request.headers['authorization'], headers['Authorization'])
            self.assertEqual(request.headers['tenant-id'], str(self.identity.tenant_id))
            return httpx.Response(status, json=body)

        remote = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(auth_api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api.httpx, "AsyncClient", return_value=remote):
            return await self.client.get(self.path, headers=headers)

    async def test_only_authorized_names_are_returned_without_connection_or_fields(self):
        body = {"code": 0, "data": [{"id": 1, "name": "销售测试", "host": "private-host",
                 "password": "secret", "tables": [{"tableId": 7, "name": "orders",
                 "categoryName": "销售订单", "comment": "已登记的订单记录",
                 "fields": [{"name": "private_field"}]}]}]}
        response = await self.request(body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(response.json(), {"tables": [{"name": "销售订单", "description": "已登记的订单记录",
                         "question": "查看销售测试中的销售订单的前10条数据"}], "total": 1})
        self.assertNotIn("secret", response.text)
        self.assertNotIn("private-host", response.text)
        self.assertNotIn("private_field", response.text)

    async def test_no_permission_and_revocation_return_empty_without_cached_names(self):
        first = await self.request({"code": 0, "data": [{"id": 1, "name": "测试", "tables": [
            {"name": "orders", "comment": "订单"}]}]})
        self.assertEqual(first.json()['total'], 1)
        revoked = await self.request({"code": 0, "data": []})
        self.assertEqual(revoked.json(), {"tables": [], "total": 0})

    async def test_same_names_in_other_sources_are_distinct_and_limit_is_six(self):
        sources = [{"id": i, "name": f"来源{i}", "tables": [
            {"name": "orders", "tableId": i, "categoryName": "订单"}] * 2} for i in range(8)]
        response = await self.request({"code": 0, "data": sources})
        self.assertEqual(response.json()['total'], 8)
        self.assertEqual(len(response.json()['tables']), 6)
        self.assertEqual(response.json()['tables'][0]['question'], "查看来源0中的订单的前10条数据")

    async def test_anonymous_invalid_and_expired_tokens_never_fetch_catalog(self):
        self.assertEqual((await self.client.get(self.path)).status_code, 401)
        with patch.object(auth_api, "resolve_platform_token_identity", AsyncMock(return_value=None)), \
             patch.object(api.httpx, "AsyncClient") as remote:
            for token in ('invalid', 'expired'):
                response = await self.client.get(self.path, headers={'Authorization': f'Bearer {token}'})
                self.assertEqual(response.status_code, 401)
            remote.assert_not_called()

    async def test_token_tenant_controls_catalog_with_missing_or_untrusted_header(self):
        for verified_tenant in (124, 125):
            self.identity = SimpleNamespace(tenant_id=verified_tenant, user_id=361)
            for tenant in (None, '', 'invalid', '124', '125'):
                with self.subTest(verified_tenant=verified_tenant, tenant=tenant):
                    headers = {'Authorization': f'Bearer user-{verified_tenant}'}
                    if tenant is not None:
                        headers['tenant-id'] = tenant
                    response = await self.request({'code': 0, 'data': []}, headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'tables': [], 'total': 0})

    async def test_permission_service_failure_never_falls_back_to_local_or_index(self):
        for status, body, expected in [(403, {}, 403), (200, {'code': 401}, 401),
                                       (500, {}, 502), (200, {'code': 0, 'data': {}}, 502)]:
            response = await self.request(body, status)
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('tables', response.json())

    async def test_timeout_is_service_error_not_empty_permission_result(self):
        def handle(request):
            raise httpx.ReadTimeout("private endpoint details")
        remote = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with patch.object(auth_api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api.httpx, "AsyncClient", return_value=remote):
            response = await self.client.get(self.path, headers=self.headers)
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private endpoint", response.text)

    async def test_disabled_platform_does_not_fetch_catalog(self):
        with patch.object(api.settings, "PLATFORM_DATASOURCE_ENABLED", False), \
             patch.object(auth_api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api.httpx, "AsyncClient") as remote:
            response = await self.client.get(self.path, headers=self.headers)
        self.assertEqual(response.status_code, 503)
        remote.assert_not_called()
