import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from apps.datasource.api import platform_embeddings as api


class ApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(api.router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.identity = SimpleNamespace(tenant_id=1, user_id=7)
        self.settings = patch.object(api.settings, "PLATFORM_AUTH_ENABLED", True)
        self.settings.start()

    async def asyncTearDown(self):
        await self.client.aclose()
        self.settings.stop()

    async def test_anonymous_expired_and_wrong_tenant(self):
        path = "/platform/table-embeddings/sync?datasourceId=3"
        self.assertEqual((await self.client.get(path)).status_code, 401)
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=None)):
            self.assertEqual((await self.client.get(path, headers={"Authorization":"Bearer expired", "tenant-id":"1"})).status_code, 401)
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)):
            self.assertEqual((await self.client.get(path, headers={"Authorization":"Bearer test", "tenant-id":"2"})).status_code, 403)

    async def test_forbidden_submission_does_not_enqueue(self):
        from fastapi import HTTPException
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "platform_get", AsyncMock(side_effect=HTTPException(403, "Denied"))), \
             patch.object(api, "get_platform_index") as index:
            response = await self.client.post("/platform/table-embeddings/sync", json={"datasourceId":3},
                headers={"Authorization":"Bearer test", "tenant-id":"1"})
            self.assertEqual(response.status_code, 403)
            index.assert_not_called()

    async def test_incomplete_snapshot_does_not_enqueue(self):
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "platform_get", AsyncMock(return_value={"complete":False})), \
             patch.object(api, "get_platform_index") as index:
            response = await self.client.post("/platform/table-embeddings/sync", json={"datasourceId":3},
                headers={"Authorization":"Bearer test", "tenant-id":"1"})
            self.assertEqual(response.status_code, 502)
            index.assert_not_called()

    async def test_job_query_uses_tenant_and_rechecks_permission(self):
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "check_access", AsyncMock()) as access, \
             patch.object(api, "get_platform_index") as index:
            index.return_value.get.return_value = {"jobId":"a", "datasourceId":3, "status":"running"}
            response = await self.client.get("/platform/table-embeddings/sync/a", headers={"Authorization":"Bearer test", "tenant-id":"1"})
            self.assertEqual(response.status_code, 200)
            index.return_value.get.assert_called_once_with(1, "a")
            access.assert_awaited_once()

    async def test_cross_tenant_job_is_not_found(self):
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "get_platform_index") as index:
            index.return_value.get.return_value = None
            response = await self.client.get("/platform/table-embeddings/sync/other-tenant-job", headers={"Authorization":"Bearer test", "tenant-id":"1"})
            self.assertEqual(response.status_code, 404)
