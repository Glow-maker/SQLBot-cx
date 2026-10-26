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

    async def test_anonymous_invalid_and_expired_tokens_are_unauthorized(self):
        path = "/platform/table-embeddings/sync?datasourceId=3"
        self.assertEqual((await self.client.get(path)).status_code, 401)
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=None)), \
             patch.object(api, "get_platform_index") as index:
            for token in ("invalid", "expired"):
                self.assertEqual((await self.client.get(path, headers={"Authorization":f"Bearer {token}", "tenant-id":"1"})).status_code, 401)
            index.assert_not_called()

    async def test_token_tenant_controls_latest_job_and_upstream_data_access(self):
        for verified_tenant in (1, 2):
            identity = SimpleNamespace(tenant_id=verified_tenant, user_id=7)
            for tenant in (None, "", "invalid", "1", "2"):
                with self.subTest(verified_tenant=verified_tenant, tenant=tenant):
                    headers = {"Authorization": f"Bearer user-{verified_tenant}"}
                    if tenant is not None:
                        headers["tenant-id"] = tenant

                    def handle(request):
                        self.assertEqual(str(request.url), "http://data.test/openapi/sqlbot/datasources/3/embedding-access")
                        self.assertEqual(request.headers["authorization"], headers["Authorization"])
                        self.assertEqual(request.headers["tenant-id"], str(verified_tenant))
                        return httpx.Response(200, json={"code": 0, "data": {
                            "tenantId": verified_tenant, "datasourceId": 3}})

                    remote = httpx.AsyncClient(transport=httpx.MockTransport(handle))
                    with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=identity)), \
                         patch.object(api.settings, "PLATFORM_DATASOURCE_ENABLED", True), \
                         patch.object(api.settings, "PLATFORM_DATASOURCE_BASE_URL", "http://data.test"), \
                         patch.object(api.httpx, "AsyncClient", return_value=remote), \
                         patch.object(api, "get_platform_index") as index:
                        index.return_value.get.return_value = None
                        response = await self.client.get("/platform/table-embeddings/sync?datasourceId=3", headers=headers)
                        self.assertEqual(response.status_code, 200)
                        index.return_value.get.assert_called_once_with(verified_tenant, None, 3)

    async def test_forbidden_submission_does_not_enqueue(self):
        from fastapi import HTTPException
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "platform_get", AsyncMock(side_effect=HTTPException(403, "Denied"))), \
             patch.object(api, "get_platform_index") as index:
            response = await self.client.post("/platform/table-embeddings/sync", json={"datasourceId":3},
                headers={"Authorization":"Bearer test", "tenant-id":"2"})
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

    async def test_foreign_tenant_or_datasource_snapshot_does_not_enqueue(self):
        for tenant, datasource in ((2, 3), (1, 4)):
            snapshot = {"tenantId": tenant, "datasourceId": datasource, "datasourceName": "test",
                        "datasourceType": "pg", "complete": True, "tableIds": [], "tables": []}
            with self.subTest(tenant=tenant, datasource=datasource), \
                 patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
                 patch.object(api, "platform_get", AsyncMock(return_value=snapshot)), \
                 patch.object(api, "get_platform_index") as index:
                response = await self.client.post("/platform/table-embeddings/sync", json={"datasourceId":3},
                    headers={"Authorization":"Bearer test", "tenant-id":"2"})
                self.assertEqual(response.status_code, 403)
                index.assert_not_called()

    async def test_submission_uses_token_tenant_without_tenant_header(self):
        snapshot = {"tenantId": 1, "datasourceId": 3, "datasourceName": "test",
                    "datasourceType": "pg", "complete": True, "tableIds": [], "tables": [], "failures": []}
        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api, "platform_get", AsyncMock(return_value=snapshot)) as upstream, \
             patch.object(api, "get_platform_index") as index:
            index.return_value.submit.return_value = {"jobId": "own-job", "datasourceId": 3, "status": "pending"}
            response = await self.client.post("/platform/table-embeddings/sync", json={"datasourceId":3},
                headers={"Authorization":"Bearer test"})
            self.assertEqual(response.status_code, 202)
            upstream.assert_awaited_once_with(3, (self.identity, "test"), "embedding-metadata",
                                              api.settings.PLATFORM_INDEX_METADATA_TIMEOUT_SECONDS)
            index.return_value.submit.assert_called_once_with(1, 7, snapshot, False)

    async def test_only_index_snapshot_fetch_uses_longer_timeout(self):
        snapshot = {"tenantId": 1, "datasourceId": 3, "datasourceName": "test",
                    "datasourceType": "pg", "complete": True, "tableIds": [], "tables": [], "failures": []}
        timeouts = {}
        client_class = httpx.AsyncClient

        def remote(**kwargs):
            def handle(request):
                operation = request.url.path.rsplit("/", 1)[-1]
                timeouts[operation] = kwargs["timeout"]
                data = snapshot if operation == "embedding-metadata" else {"tenantId": 1, "datasourceId": 3}
                return httpx.Response(200, json={"code": 0, "data": data})
            return client_class(transport=httpx.MockTransport(handle))

        with patch.object(api, "resolve_platform_token_identity", AsyncMock(return_value=self.identity)), \
             patch.object(api.settings, "PLATFORM_DATASOURCE_ENABLED", True), \
             patch.object(api.settings, "PLATFORM_DATASOURCE_BASE_URL", "http://data.test"), \
             patch.object(api.settings, "PLATFORM_DATASOURCE_HTTP_TIMEOUT_SECONDS", 10.0), \
             patch.object(api.settings, "PLATFORM_INDEX_METADATA_TIMEOUT_SECONDS", 90.0), \
             patch.object(api.httpx, "AsyncClient", side_effect=remote), \
             patch.object(api, "get_platform_index") as index:
            index.return_value.submit.return_value = {"jobId": "job", "datasourceId": 3, "status": "pending"}
            index.return_value.get.return_value = None
            headers = {"Authorization": "Bearer test"}
            self.assertEqual((await self.client.post("/platform/table-embeddings/sync", json={"datasourceId": 3},
                                                     headers=headers)).status_code, 202)
            self.assertEqual((await self.client.get("/platform/table-embeddings/sync?datasourceId=3",
                                                    headers=headers)).status_code, 200)
        self.assertEqual(timeouts, {"embedding-metadata": 90.0, "embedding-access": 10.0})

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
             patch.object(api, "check_access", AsyncMock()) as access, \
             patch.object(api, "get_platform_index") as index:
            index.return_value.get.return_value = None
            response = await self.client.get("/platform/table-embeddings/sync/other-tenant-job", headers={"Authorization":"Bearer test", "tenant-id":"2"})
            self.assertEqual(response.status_code, 404)
            index.return_value.get.assert_called_once_with(1, "other-tenant-job")
            access.assert_not_awaited()
