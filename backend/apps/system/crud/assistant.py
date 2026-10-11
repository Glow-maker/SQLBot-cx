import json
import re
import threading
import time
import urllib
from typing import Any, Optional

import requests
from fastapi import FastAPI
from sqlalchemy import Engine, create_engine
from sqlmodel import Session, select
from starlette.middleware.cors import CORSMiddleware

# from apps.datasource.embedding.table_embedding import get_table_embedding
from apps.datasource.models.datasource import CoreDatasource, DatasourceConf
from apps.datasource.utils.utils import aes_encrypt
from apps.system.models.system_model import AssistantModel
from apps.system.schemas.auth import CacheName, CacheNamespace
from apps.system.schemas.system_schema import AssistantHeader, AssistantOutDsSchema, UserInfoDTO
from common.core.config import settings
from common.core.db import engine
from common.core.sqlbot_cache import cache
from common.utils.aes_crypto import simple_aes_decrypt
from common.utils.utils import SQLBotLogUtil, equals_ignore_case, get_domain_list, string_to_numeric_hash
from common.core.deps import Trans


@cache(namespace=CacheNamespace.EMBEDDED_INFO, cacheName=CacheName.ASSISTANT_INFO, keyExpression="assistant_id")
async def get_assistant_info(*, session: Session, assistant_id: int) -> AssistantModel | None:
    db_model = session.get(AssistantModel, assistant_id)
    return db_model


def get_assistant_user(*, id: int):
    return UserInfoDTO(id=id, account="sqlbot-inner-assistant", oid=1, name="sqlbot-inner-assistant",
                       email="sqlbot-inner-assistant@sqlbot.com")


def get_assistant_ds(session: Session, llm_service) -> list[dict]:
    assistant: AssistantHeader = llm_service.current_assistant
    type = assistant.type
    if type == 0 or type == 2:
        configuration = assistant.configuration
        if configuration:
            config: dict[any] = json.loads(configuration)
            oid: int = int(config['oid'])
            stmt = select(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description).where(
                CoreDatasource.oid == oid)
            if not assistant.online:
                public_list: list[int] = config.get('public_list') or None
                if public_list:
                    stmt = stmt.where(CoreDatasource.id.in_(public_list))
                else:
                    return []
                """ private_list: list[int] = config.get('private_list') or None
                if private_list:
                    stmt = stmt.where(~CoreDatasource.id.in_(private_list)) """
        db_ds_list = session.exec(stmt)

        result_list = [
            {
                "id": ds.id,
                "name": ds.name,
                "description": ds.description
            }
            for ds in db_ds_list
        ]

        # filter private ds if offline
        return result_list
    out_ds_instance: AssistantOutDs = AssistantOutDsFactory.get_instance(assistant)
    llm_service.out_ds_instance = out_ds_instance
    dslist = out_ds_instance.get_simple_ds_list()
    # format?
    return dslist


def init_dynamic_cors(app: FastAPI):
    try:
        with Session(engine) as session:
            list_result = session.exec(select(AssistantModel).order_by(AssistantModel.create_time)).all()
            seen = set()
            unique_domains = []
            for item in list_result:
                if item.domain:
                    for domain in get_domain_list(item.domain):
                        domain = domain.strip()
                        if domain and domain not in seen:
                            seen.add(domain)
                            unique_domains.append(domain)
            cors_middleware = None
            for middleware in app.user_middleware:
                if middleware.cls == CORSMiddleware:
                    cors_middleware = middleware
                    break
            if cors_middleware:
                updated_origins = list(set(settings.all_cors_origins + unique_domains))
                cors_middleware.kwargs['allow_origins'] = updated_origins
    except Exception as e:
        return False, e


# 中台 datasource 拉取、物理源 key、schema.table 上下文。
class AssistantOutDs:
    assistant: AssistantHeader
    ds_list: Optional[list[AssistantOutDsSchema]] = None
    certificate: Optional[str] = None
    request_origin: Optional[str] = None

    def __init__(self, assistant: AssistantHeader):
        self.assistant = assistant
        self.ds_list = None
        self.certificate = assistant.certificate
        self.request_origin = assistant.request_origin
        started = time.perf_counter()
        self.get_ds_from_api()
        self.metadata_load_ms = (time.perf_counter() - started) * 1000

    # @cache(namespace=CacheNamespace.EMBEDDED_INFO, cacheName=CacheName.ASSISTANT_DS, keyExpression="current_user.id")
    def get_ds_from_api(self):
        config: dict[any] = json.loads(self.assistant.configuration)
        endpoint: str = config['endpoint']
        endpoint = self.get_complete_endpoint(endpoint=endpoint)
        if not endpoint:
            raise Exception(
                f"Failed to get datasource list from {config['endpoint']}, error: [Assistant domain or endpoint miss]")
        certificateList: list[any] = json.loads(self.certificate)
        header = {}
        cookies = {}
        param = {}
        for item in certificateList:
            if item['target'] == 'header':
                header[item['key']] = item['value']
            if item['target'] == 'cookie':
                cookies[item['key']] = item['value']
            if item['target'] == 'param':
                param[item['key']] = item['value']
        timeout = int(config.get('timeout')) if config.get('timeout') else 10
        res = requests.get(url=endpoint, params=param, headers=header, cookies=cookies, timeout=timeout)
        if res.status_code == 200:
            result_json: dict[any] = json.loads(res.text)
            if result_json.get('code') == 0 or result_json.get('code') == 200:
                temp_list = result_json.get('data', [])
                temp_ds_list = [
                    self.convert2schema(item, config)
                    for item in temp_list
                ]
                self.ds_list = temp_ds_list
                return self.ds_list
            else:
                raise Exception(f"Failed to get datasource list from {endpoint}, error: {result_json.get('message')}")
        else:
            SQLBotLogUtil.error(f"Failed to get datasource list from {endpoint}, response: {res}")
            raise Exception(f"Failed to get datasource list from {endpoint}, response: {res}")

    def get_first_element(self, text: str):
        parts = re.split(r'[,;]', text.strip())
        first_domain = parts[0].strip()
        return first_domain

    def get_complete_endpoint(self, endpoint: str) -> str | None:
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        domain_text = self.assistant.domain
        if not domain_text:
            return None
        if ',' in domain_text or ';' in domain_text:
            return (
                self.request_origin.strip('/') if self.request_origin else self.get_first_element(domain_text).strip(
                    '/')) + endpoint
        else:
            return f"{domain_text}{endpoint}"

    def get_simple_ds_list(self):
        if self.ds_list:
            return [{'id': ds.id, 'name': ds.name, 'description': ds.comment} for ds in self.ds_list]
        else:
            raise Exception("Datasource list is not found.")
        
    # 生成 SQL 上下文时，同物理源下多个 datasource/schema 会合并
    def get_db_schema(self, ds_id: int, question: str = '', embedding: bool = True,
                      table_list: list[Any] = None, physical_datasource_key: str = None) -> str:
        ds = self.get_ds(ds_id)
        target_ds_list = self.get_physical_group_ds(ds, physical_datasource_key) if physical_datasource_key else [ds]
        schema_str = ""
        db_name = physical_datasource_key or (ds.db_schema if ds.db_schema is not None and ds.db_schema != "" else ds.dataBase)
        schema_str += f"【DB_ID】 {db_name}\n【Schema】\n"
        tables = []
        i = 0
        for ds_item in target_ds_list:
            for table in ds_item.tables or []:
                # 如果传入了 table_list，则只处理在列表中的表
                if table_list is not None and not self._table_matches_filter(ds_item, table, table_list):
                    continue

                i += 1
                schema_table = ''
                full_table_name = self._qualified_table_name(ds_item, table)
                schema_table += f"# Table: {full_table_name}"
                table_comment = table.comment
                if table_comment == '':
                    schema_table += '\n[\n'
                else:
                    schema_table += f", {table_comment}\n[\n"

                field_list = []
                for field in table.fields or []:
                    field_comment = field.comment
                    if field_comment == '':
                        field_list.append(f"({field.name}:{field.type})")
                    else:
                        field_list.append(f"({field.name}:{field.type}, {field_comment})")
                schema_table += ",\n".join(field_list)
                schema_table += '\n]\n'
                t_obj = {"id": i, "schema_table": schema_table}
                tables.append(t_obj)

        # do table embedding
        # if embedding and tables and settings.TABLE_EMBEDDING_ENABLED:
        #     tables = get_table_embedding(tables, question)

        if tables:
            for s in tables:
                schema_str += s.get('schema_table')

        return schema_str

    @staticmethod
    def _physical_datasource_key(ds: AssistantOutDsSchema) -> str:
        key = getattr(ds, "physicalDatasourceKey", None)
        if key is not None and str(key).strip() != "":
            return str(key)
        return f"datasource:{getattr(ds, 'id', '')}"

    # 同一执行连接必须匹配类型、主机、端口、数据库和查询账号；信息不完整时按登记数据源隔离。
    @staticmethod
    def _build_physical_datasource_key(ds_dict: dict, ds_id: Any) -> str:
        import hashlib
        ds_type = str(ds_dict.get("type") or ds_dict.get("type_name") or "").strip().lower()
        host = str(ds_dict.get("host") or "").strip().lower()
        port = str(ds_dict.get("port") or "").strip()
        database = str(ds_dict.get('dataBase') or '').strip()
        principal = str(ds_dict.get('user') or '').strip()
        if ds_type and host and port and database and principal:
            # A shared PG instance is not a shared query connection. Different
            # databases or principals cannot participate in the same SQL.
            identity = json.dumps([ds_type, host, port, database, principal])
            return 'source:' + hashlib.sha256(identity.encode()).hexdigest()
        return f"datasource:{ds_id}"

    def get_physical_group_ds(self, ds: AssistantOutDsSchema, physical_datasource_key: str = None) -> list[AssistantOutDsSchema]:
        target_key = physical_datasource_key or self._physical_datasource_key(ds)
        return [
            item for item in (self.ds_list or [])
            if self._physical_datasource_key(item) == target_key
        ] or [ds]

    @staticmethod
    def _table_schema_name(ds: AssistantOutDsSchema, table) -> str:
        schema_name = getattr(table, "schemaName", None)
        if schema_name is not None and str(schema_name).strip() != "":
            return str(schema_name)
        if ds.db_schema is not None and str(ds.db_schema).strip() != "":
            return str(ds.db_schema)
        if ds.dataBase is not None and str(ds.dataBase).strip() != "":
            return str(ds.dataBase)
        return ""

    @classmethod
    def _qualified_table_name(cls, ds: AssistantOutDsSchema, table) -> str:
        table_name = str(getattr(table, "name", "") or "")
        schema_name = cls._table_schema_name(ds, table)
        return f"{schema_name}.{table_name}" if schema_name else table_name

    @classmethod
    def _table_matches_filter(cls, ds: AssistantOutDsSchema, table, table_list: list[Any]) -> bool:
        table_name = str(getattr(table, "name", "") or "").lower()
        table_id = getattr(table, "tableId", None) or getattr(table, "id", None)
        schema_name = cls._table_schema_name(ds, table).lower()
        qualified_name = cls._qualified_table_name(ds, table).lower()

        for item in table_list:
            if isinstance(item, dict):
                ref_ds_id = item.get("datasourceId") or item.get("datasource_id") or item.get("dataSourceId")
                if ref_ds_id is not None and str(ref_ds_id) != str(getattr(ds, "id", "")):
                    continue

                ref_table_id = item.get("tableId") or item.get("id")
                if ref_table_id is not None and table_id is not None and str(ref_table_id) == str(table_id):
                    return True

                ref_name = str(item.get("name") or item.get("tableName") or "").lower()
                ref_schema = str(item.get("schemaName") or item.get("schema") or item.get("db_schema") or "").lower()
                if ref_name and ref_name == table_name and (not ref_schema or ref_schema == schema_name):
                    return True
                continue

            ref = str(item).lower()
            if ref in {table_name, qualified_name, str(table_id).lower() if table_id is not None else ""}:
                return True
        return False

    def get_ds(self, ds_id: int, trans: Trans = None):
        if self.ds_list:
            for ds in self.ds_list:
                if ds.id == ds_id:
                    return ds
        else:
            raise Exception("Datasource list is not found.")
        raise Exception(f"Datasource id {ds_id} is not found." if trans is None else trans(
            'i18n_data_training.datasource_id_not_found', key=ds_id))

    # 将中台返回的 ds_dict 转换为 AssistantOutDsSchema，同时处理加密字段和物理源 key 构建。
    def convert2schema(self, ds_dict: dict, config: dict[any]) -> AssistantOutDsSchema:
        id_marker: str = ''
        attr_list = ['name', 'type', 'host', 'port', 'user', 'dataBase', 'schema', 'mode']
        if config.get('encrypt', False):
            key = config.get('aes_key', None)
            iv = config.get('aes_iv', None)
            aes_attrs = ['host', 'user', 'password', 'dataBase', 'db_schema', 'schema', 'mode']
            for attr in aes_attrs:
                if attr in ds_dict and ds_dict[attr]:
                    try:
                        ds_dict[attr] = simple_aes_decrypt(ds_dict[attr], key, iv)
                    except Exception as e:
                        raise Exception(
                            f"Failed to encrypt {attr} for datasource {ds_dict.get('name')}, error: {str(e)}")
        
        id = ds_dict.get('id', None)
        if not id:
            for attr in attr_list:
                if attr in ds_dict:
                    id_marker += str(ds_dict.get(attr, '')) + '--sqlbot--'
            id = string_to_numeric_hash(id_marker)
        db_schema = ds_dict.get('schema', ds_dict.get('db_schema', ''))
        ds_dict.pop("schema", None)
        physical_datasource_key = self._build_physical_datasource_key(ds_dict, id)
        return AssistantOutDsSchema(**{
            **ds_dict,
            "id": id,
            "db_schema": db_schema,
            "physicalDatasourceKey": physical_datasource_key,
        })


class AssistantOutDsFactory:
    """
    主线二 Phase 2a：进程内 TTL 缓存 AssistantOutDs 实例，避免每次请求都同步 HTTP 拉取
    中台 data 服务（高 QPS 场景下会打爆中台）。

    缓存键：(assistant.id, assistant.oid, configuration_hash, certificate_hash)
    - 同一用户 + 同一 endpoint + 同一 token 的并发请求复用同一份 ds_list
    - token 变化（Authorization header 不同）→ certificate_hash 变 → 新建实例
    - 开关关闭或 TTL=0 时退化为每次新建（与改造前行为一致）
    """

    _cache: dict = {}
    _lock = threading.Lock()

    @staticmethod
    def _cache_key(assistant: AssistantHeader) -> tuple:
        return (
            getattr(assistant, "id", 0),
            getattr(assistant, "oid", 0),
            hash(assistant.configuration or ""),
            hash(assistant.certificate or ""),
        )

    @staticmethod
    def get_instance(assistant: AssistantHeader) -> AssistantOutDs:
        config = json.loads(assistant.configuration or "{}")
        if isinstance(config, dict) and config.get("platform_virtual"):
            # Current catalog is authoritative for recall, including immediately offlined tables.
            return AssistantOutDs(assistant)
        ttl = settings.PLATFORM_DATASOURCE_CACHE_TTL_SECONDS
        if not ttl or ttl <= 0:
            return AssistantOutDs(assistant)
        key = AssistantOutDsFactory._cache_key(assistant)
        now = time.time()
        with AssistantOutDsFactory._lock:
            entry = AssistantOutDsFactory._cache.get(key)
            if entry and entry[0] > now:
                return entry[1]
        # 构造在锁外执行（构造涉及阻塞 HTTP，避免锁住其他 key）
        instance = AssistantOutDs(assistant)
        with AssistantOutDsFactory._lock:
            AssistantOutDsFactory._cache[key] = (now + ttl, instance)
            # 简单清理：超过 256 项时丢弃过期项
            if len(AssistantOutDsFactory._cache) > 256:
                AssistantOutDsFactory._cache = {
                    k: v for k, v in AssistantOutDsFactory._cache.items() if v[0] > now
                }
        return instance


def get_out_ds_conf(ds: AssistantOutDsSchema, timeout: int = 30) -> str:
    conf = {
        "host": ds.host or '',
        "port": ds.port or 0,
        "username": ds.user or '',
        "password": ds.password or '',
        "database": ds.dataBase or '',
        "driver": '',
        "extraJdbc": ds.extraParams or '',
        "dbSchema": ds.db_schema or '',
        "timeout": timeout or 30,
        "mode": ds.mode or ''
    }
    conf["extraJdbc"] = ''
    return aes_encrypt(json.dumps(conf))
