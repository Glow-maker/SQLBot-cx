import concurrent
import json
import os
import re
import time
import traceback
import urllib.parse
import warnings
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime
from typing import Any, List, Optional, Union, Dict, Iterator

import orjson
import pandas as pd
import requests
import sqlparse
from langchain.chat_models.base import BaseChatModel
from langchain_community.utilities import SQLDatabase
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, BaseMessageChunk
from openai import APITimeoutError
from sqlalchemy import and_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlbot_xpack.config.model import SysArgModel
from sqlbot_xpack.custom_prompt.curd.custom_prompt import find_custom_prompts
from sqlbot_xpack.custom_prompt.models.custom_prompt_model import CustomPromptTypeEnum
from sqlbot_xpack.license.license_manage import SQLBotLicenseUtil
from sqlmodel import Session

from apps.chat.task.progress import ChatProgress
from apps.chat.task.query_guard import bounded_stream, is_greeting, require_read_query
from apps.datasource.embedding.platform_index import get_platform_index
from apps.datasource.embedding.platform_recall import exact_name, fuse, routing_card
from apps.ai_model.model_factory import LLMConfig, LLMFactory, get_default_config
from common.utils.platform_permission import (
    DeniedTable, PermissionCheckResult, check_table_permissions, denied_tables_to_sse_payload, is_permission_check_enabled,
)
from common.utils.sql_parser import SQLTableRef, extract_table_refs
from apps.chat.curd.chat import save_question, save_sql_answer, save_sql, \
    save_error_message, save_sql_exec_data, save_chart_answer, save_chart, \
    finish_record, save_analysis_answer, save_predict_answer, save_predict_data, \
    save_select_datasource_answer, save_recommend_question_answer, \
    get_old_questions, save_analysis_predict_record, rename_chat, get_chart_config, \
    get_chat_chart_data, list_generate_sql_logs, list_generate_chart_logs, start_log, end_log, \
    get_last_execute_sql_error, format_json_data, format_chart_fields, get_chat_brief_generate, get_chat_predict_data, \
    get_chat_chart_config, trigger_log_error
from apps.chat.models.chat_model import ChatQuestion, ChatRecord, Chat, RenameChat, ChatLog, OperationEnum, \
    ChatFinishStep, AxisObj
from apps.data_training.curd.data_training import get_training_template
from apps.datasource.crud.datasource import get_table_schema
from apps.datasource.crud.permission import get_row_permission_filters, is_normal_user
from apps.datasource.embedding.ds_embedding import get_ds_embedding
from apps.datasource.models.datasource import CoreDatasource
from apps.db.db import exec_sql, get_version, check_connection
from apps.system.crud.assistant import AssistantOutDs, AssistantOutDsFactory, get_assistant_ds
from apps.system.crud.parameter_manage import get_groups
from apps.system.schemas.system_schema import AssistantOutDsSchema
from apps.terminology.curd.terminology import get_terminology_template
from common.core.config import settings
from common.core.db import engine
from common.core.deps import CurrentAssistant, CurrentUser
from common.error import SingleMessageError, SQLBotDBError, ParseSQLResultError, SQLBotDBConnectionError
from common.utils.data_format import DataFormat
from common.utils.locale import I18n, I18nHelper
from common.utils.utils import SQLBotLogUtil, extract_nested_json, prepare_for_orjson

warnings.filterwarnings("ignore")

executor = ThreadPoolExecutor(max_workers=200)

dynamic_ds_types = [1, 3]
dynamic_subsql_prefix = 'select * from sqlbot_dynamic_temp_table_'

session_maker = scoped_session(sessionmaker(bind=engine, class_=Session))

i18n = I18n()


class LLMService:
    ds: CoreDatasource
    chat_question: ChatQuestion
    record: ChatRecord
    config: LLMConfig
    llm: BaseChatModel
    sql_message: List[Union[BaseMessage, dict[str, Any]]] = []
    chart_message: List[Union[BaseMessage, dict[str, Any]]] = []

    # session: Session = db_session
    current_user: CurrentUser
    current_assistant: Optional[CurrentAssistant] = None
    out_ds_instance: Optional[AssistantOutDs] = None
    change_title: bool = False

    generate_sql_logs: List[ChatLog] = []
    generate_chart_logs: List[ChatLog] = []

    current_logs: dict[OperationEnum, ChatLog] = {}

    chunk_list: List[str] = []
    future: Future

    trans: I18nHelper = None

    last_execute_sql_error: str = None
    articles_number: int = 4

    enable_sql_row_limit: bool = settings.GENERATE_SQL_QUERY_LIMIT_ENABLED
    base_message_round_count_limit: int = settings.GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT

    def __init__(self, session: Session, current_user: CurrentUser, chat_question: ChatQuestion,
                 current_assistant: Optional[CurrentAssistant] = None, no_reasoning: bool = False,
                 embedding: bool = False, config: LLMConfig = None):
        self.current_logs = {}
        self.sql_message = []
        self.chart_message = []
        self.out_ds_instance = None
        self.chunk_list = []
        self.current_user = current_user
        self.current_assistant = current_assistant
        self.logical_route_table_names: Optional[list[str]] = None
        self.logical_route_table_refs: Optional[list[dict[str, Any]]] = None
        self.logical_route_tables: list[dict[str, Any]] = []
        self.logical_route_physical_datasource_key: Optional[str] = None
        chat_id = chat_question.chat_id
        chat: Chat | None = session.get(Chat, chat_id)
        if not chat:
            raise SingleMessageError(f"Chat with id {chat_id} not found")
        if chat.create_by != current_user.id or chat.oid != current_user.oid:
            raise SingleMessageError("无权访问此会话")
        self.query_deadline = time.monotonic() + 120
        ds: CoreDatasource | AssistantOutDsSchema | None = None
        if not chat.datasource and chat_question.datasource_id:
            _ds = session.get(CoreDatasource, chat_question.datasource_id)
            if _ds:
                if _ds.oid != current_user.oid:
                    raise SingleMessageError(
                        f"Datasource with id {chat_question.datasource_id} does not belong to current workspace")
                chat.datasource = _ds.id
                chat.engine_type = _ds.type_name
                # save chat
                session.add(chat)
                session.flush()
                session.refresh(chat)
                session.commit()

        if chat.datasource:
            # Get available datasource
            if current_assistant and current_assistant.type in dynamic_ds_types:
                self.out_ds_instance = AssistantOutDsFactory.get_instance(current_assistant)
                ds = self.out_ds_instance.get_ds(chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                chat_question.engine = ds.type + get_version(ds)
            else:
                ds = session.get(CoreDatasource, chat.datasource)
                if not ds:
                    raise SingleMessageError("No available datasource configuration found")
                chat_question.engine = (ds.type_name if ds.type != 'excel' else 'PostgreSQL') + get_version(ds)

        self.generate_sql_logs = list_generate_sql_logs(session=session, chart_id=chat_id)
        self.generate_chart_logs = list_generate_chart_logs(session=session, chart_id=chat_id)

        self.change_title = not get_chat_brief_generate(session=session, chat_id=chat_id)

        chat_question.lang = get_lang_name(current_user.language)
        self.trans = i18n(lang=current_user.language)

        self.ds = (
            ds if isinstance(ds, AssistantOutDsSchema) else CoreDatasource(**ds.model_dump())) if ds else None
        self.chat_question = chat_question
        self.config = config
        if no_reasoning:
            # only work while using qwen
            if self.config.additional_params:
                if self.config.additional_params.get('extra_body'):
                    if self.config.additional_params.get('extra_body').get('enable_thinking'):
                        del self.config.additional_params['extra_body']['enable_thinking']

        self.chat_question.ai_modal_id = self.config.model_id
        self.chat_question.ai_modal_name = self.config.model_name

        # Create LLM instance through factory
        llm_instance = LLMFactory.create_llm(self.config)
        self.llm = llm_instance.llm

        # get last_execute_sql_error
        last_execute_sql_error = get_last_execute_sql_error(session, self.chat_question.chat_id)
        if last_execute_sql_error:
            self.chat_question.error_msg = f'''<error-msg>
{last_execute_sql_error}
</error-msg>'''
        else:
            self.chat_question.error_msg = ''

    @classmethod
    async def create(cls, *args, **kwargs):
        config: LLMConfig = await get_default_config()
        instance = cls(*args, **kwargs, config=config)

        chat_params: list[SysArgModel] = await get_groups(args[0], "chat")
        for config in chat_params:
            if config.pkey == 'chat.limit_rows':
                if config.pval.lower().strip() == 'true':
                    instance.enable_sql_row_limit = True
                else:
                    instance.enable_sql_row_limit = False
            if config.pkey == 'chat.context_record_count':
                count_value = config.pval
                if count_value is None:
                    count_value = settings.GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT
                count_value = int(count_value)
                if count_value < 0:
                    count_value = 0
                instance.base_message_round_count_limit = count_value
        return instance

    def is_running(self, timeout=0.5):
        try:
            r = concurrent.futures.wait([self.future], timeout)
            if len(r.not_done) > 0:
                return True
            else:
                return False
        except Exception as e:
            return True

    def init_messages(self, session: Session):

        self.choose_table_schema(session)
        if self._logical_datasource_enabled():
            # Revalidate every historical SQL before reusing prompts that may
            # contain schema/results. One revoked round invalidates the history.
            for log in self.generate_sql_logs:
                previous = session.get(ChatRecord, log.pid)
                try:
                    from apps.chat.task.record_access import require_record_access
                    require_record_access(session, self.current_user, log.pid, current_assistant=self.current_assistant)
                    previous_ds = self._ensure_out_ds_instance().get_ds(previous.datasource)
                    same_source = (AssistantOutDs._physical_datasource_key(previous_ds)
                                   == AssistantOutDs._physical_datasource_key(self.ds))
                except Exception:
                    same_source = False
                if not previous or not previous.sql or not same_source:
                    self.generate_sql_logs = []
                    self.generate_chart_logs = []
                    self.chat_question.error_msg = ''
                    break
                check = self._do_permission_check(previous.sql, None, "history")
                if check is None or not check.allowed:
                    self.generate_sql_logs = []
                    self.generate_chart_logs = []
                    self.chat_question.error_msg = ''
                    break

        last_sql_messages: List[dict[str, Any]] = self.generate_sql_logs[-1].messages if len(
            self.generate_sql_logs) > 0 else []
        if self.chat_question.regenerate_record_id:
            # filter record before regenerate_record_id
            _temp_log = next(
                filter(lambda obj: obj.pid == self.chat_question.regenerate_record_id, self.generate_sql_logs), None)
            last_sql_messages: List[dict[str, Any]] = _temp_log.messages if _temp_log else []

        count_limit = self.base_message_round_count_limit

        self.sql_message = []
        # add sys prompt
        self.sql_message.append(SystemMessage(
            content=self.chat_question.sql_sys_question(self.ds.type, self.enable_sql_row_limit)))
        if self._logical_datasource_enabled():
            self.sql_message[0].content += ('\n当前问题已选定的数据源：' + self.ds.name
                + '。问题中的这个名称仅用于限定来源，不是表名或业务筛选条件。'
                  '请在上述正式 m-schema 中的表和字段范围内完成其余查询需求。')
        if last_sql_messages is not None and len(last_sql_messages) > 0:
            last_rounds = get_last_conversation_rounds(last_sql_messages, rounds=count_limit)

            for _msg_dict in last_rounds:
                _msg: BaseMessage
                if _msg_dict.get('type') == 'human':
                    _msg = HumanMessage(content=_msg_dict.get('content'))
                    self.sql_message.append(_msg)
                elif _msg_dict.get('type') == 'ai':
                    _msg = AIMessage(content=_msg_dict.get('content'))
                    self.sql_message.append(_msg)

        last_chart_messages: List[dict[str, Any]] = self.generate_chart_logs[-1].messages if len(
            self.generate_chart_logs) > 0 else []
        if self.chat_question.regenerate_record_id:
            # filter record before regenerate_record_id
            _temp_log = next(
                filter(lambda obj: obj.pid == self.chat_question.regenerate_record_id, self.generate_chart_logs), None)
            last_chart_messages: List[dict[str, Any]] = _temp_log.messages if _temp_log else []

        count_chart_limit = self.base_message_round_count_limit

        self.chart_message = []
        # add sys prompt
        self.chart_message.append(SystemMessage(content=self.chat_question.chart_sys_question()))
        if last_chart_messages is not None and len(last_chart_messages) > 0:
            last_rounds = get_last_conversation_rounds(last_chart_messages, rounds=count_chart_limit)

            for _msg_dict in last_rounds:
                _msg: BaseMessage
                if _msg_dict.get('type') == 'human':
                    _msg = HumanMessage(content=_msg_dict.get('content'))
                    self.chart_message.append(_msg)
                elif _msg_dict.get('type') == 'ai':
                    _msg = AIMessage(content=_msg_dict.get('content'))
                    self.chart_message.append(_msg)

    def init_record(self, session: Session) -> ChatRecord:
        self.record = save_question(session=session, current_user=self.current_user, question=self.chat_question)
        return self.record

    def get_record(self):
        return self.record

    def set_record(self, record: ChatRecord):
        self.record = record

    def set_articles_number(self, articles_number: int):
        self.articles_number = articles_number

    def get_fields_from_chart(self, _session: Session):
        chart_info = get_chart_config(_session, self.record.id)
        return format_chart_fields(chart_info)

    def filter_terminology_template(self, _session: Session, oid: int = None, ds_id: int = None):
        calculate_oid = oid
        calculate_ds_id = ds_id
        if self.current_assistant:
            calculate_oid = self.current_assistant.oid if self.current_assistant.type != 4 else self.current_user.oid
            if self.current_assistant.type == 1:
                calculate_ds_id = None
        self.current_logs[OperationEnum.FILTER_TERMS] = start_log(session=_session,
                                                                  operate=OperationEnum.FILTER_TERMS,
                                                                  record_id=self.record.id, local_operation=True)

        if self._is_platform_virtual_assistant():
            from apps.operations.knowledge import platform_templates
            self.chat_question.terminologies, term_list, _, _ = platform_templates(self, _session)
        else:
            self.chat_question.terminologies, term_list = get_terminology_template(_session, self.chat_question.question,
                                                                                   calculate_oid, calculate_ds_id)
        self.current_logs[OperationEnum.FILTER_TERMS] = end_log(session=_session,
                                                                log=self.current_logs[OperationEnum.FILTER_TERMS],
                                                                full_message=term_list)

    def filter_custom_prompts(self, _session: Session, custom_prompt_type: CustomPromptTypeEnum, oid: int = None,
                              ds_id: int = None):
        if SQLBotLicenseUtil.valid():
            calculate_oid = oid
            calculate_ds_id = ds_id
            if self.current_assistant:
                calculate_oid = self.current_assistant.oid if self.current_assistant.type != 4 else self.current_user.oid
                if self.current_assistant.type == 1:
                    calculate_ds_id = None
            self.current_logs[OperationEnum.FILTER_CUSTOM_PROMPT] = start_log(session=_session,
                                                                              operate=OperationEnum.FILTER_CUSTOM_PROMPT,
                                                                              record_id=self.record.id,
                                                                              local_operation=True)
            self.chat_question.custom_prompt, prompt_list = find_custom_prompts(_session, custom_prompt_type,
                                                                                calculate_oid,
                                                                                calculate_ds_id)
            self.current_logs[OperationEnum.FILTER_CUSTOM_PROMPT] = end_log(session=_session,
                                                                            log=self.current_logs[
                                                                                OperationEnum.FILTER_CUSTOM_PROMPT],
                                                                            full_message=prompt_list)

    def filter_training_template(self, _session: Session, oid: int = None, ds_id: int = None):
        self.current_logs[OperationEnum.FILTER_SQL_EXAMPLE] = start_log(session=_session,
                                                                        operate=OperationEnum.FILTER_SQL_EXAMPLE,
                                                                        record_id=self.record.id,
                                                                        local_operation=True)
        calculate_oid = oid
        calculate_ds_id = ds_id
        if self.current_assistant:
            calculate_oid = self.current_assistant.oid if self.current_assistant.type != 4 else self.current_user.oid
            if self.current_assistant.type == 1:
                calculate_ds_id = None
        if self._is_platform_virtual_assistant():
            from apps.operations.knowledge import platform_templates
            _, _, self.chat_question.data_training, example_list = platform_templates(self, _session)
        elif self.current_assistant and self.current_assistant.type == 1:
            self.chat_question.data_training, example_list = get_training_template(_session,
                                                                                   self.chat_question.question,
                                                                                   calculate_oid,
                                                                                   None, self.current_assistant.id)
        else:
            self.chat_question.data_training, example_list = get_training_template(_session,
                                                                                   self.chat_question.question,
                                                                                   calculate_oid,
                                                                                   calculate_ds_id)
        self.current_logs[OperationEnum.FILTER_SQL_EXAMPLE] = end_log(session=_session,
                                                                      log=self.current_logs[
                                                                          OperationEnum.FILTER_SQL_EXAMPLE],
                                                                      full_message=example_list)

    def choose_table_schema(self, _session: Session):
        self.current_logs[OperationEnum.CHOOSE_TABLE] = start_log(session=_session,
                                                                  operate=OperationEnum.CHOOSE_TABLE,
                                                                  record_id=self.record.id,
                                                                  local_operation=True)
        table_list = self.logical_route_table_refs or self.logical_route_table_names
        physical_key = self.logical_route_physical_datasource_key if self._logical_datasource_enabled() else None
        self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
            self.ds.id, self.chat_question.question, table_list=table_list,
            physical_datasource_key=physical_key) if self.out_ds_instance else get_table_schema(
            session=_session,
            current_user=self.current_user,
            ds=self.ds,
            question=self.chat_question.question,
            table_list=table_list)

        self.current_logs[OperationEnum.CHOOSE_TABLE] = end_log(session=_session,
                                                                log=self.current_logs[OperationEnum.CHOOSE_TABLE],
                                                                full_message=self.chat_question.db_schema)

    def _with_query_constraints(self, session: Session, messages):
        from apps.operations.constraints import get_enabled_constraints
        # Refresh on each model stage so editing/stopping a rule takes effect on
        # later calls; never retain duplicate rules from a retried SQL message.
        messages = [message for message in messages
                    if getattr(message, 'name', None) != 'operations_constraints']
        rules = get_enabled_constraints(session)
        if rules:
            position = 1 if messages and isinstance(messages[0], SystemMessage) else 0
            messages.insert(position, SystemMessage(content=rules, name='operations_constraints'))
        return messages

    def generate_analysis(self, _session: Session):
        fields = self.get_fields_from_chart(_session)
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(_session, self.record.id)
        self.chat_question.data = orjson.dumps(data.get('data')).decode()
        analysis_msg: List[Union[BaseMessage, dict[str, Any]]] = []

        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

        self.filter_terminology_template(_session, self.current_user.oid, ds_id)

        self.filter_custom_prompts(_session, CustomPromptTypeEnum.ANALYSIS, self.current_user.oid, ds_id)

        analysis_msg.append(SystemMessage(content=self.chat_question.analysis_sys_question()))
        analysis_msg.append(HumanMessage(content=self.chat_question.analysis_user_question()))
        analysis_msg = self._with_query_constraints(_session, analysis_msg)

        self.current_logs[OperationEnum.ANALYSIS] = start_log(session=_session,
                                                              ai_modal_id=self.chat_question.ai_modal_id,
                                                              ai_modal_name=self.chat_question.ai_modal_name,
                                                              operate=OperationEnum.ANALYSIS,
                                                              record_id=self.record.id,
                                                              full_message=[
                                                                  {'type': msg.type,
                                                                   'content': msg.content} for
                                                                  msg
                                                                  in analysis_msg])
        full_thinking_text = ''
        full_analysis_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(analysis_msg, operation=OperationEnum.ANALYSIS), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_analysis_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        analysis_msg.append(AIMessage(full_analysis_text))

        self.current_logs[OperationEnum.ANALYSIS] = end_log(session=_session,
                                                            log=self.current_logs[
                                                                OperationEnum.ANALYSIS],
                                                            full_message=[
                                                                {'type': msg.type,
                                                                 'content': msg.content}
                                                                for msg in analysis_msg],
                                                            reasoning_content=full_thinking_text,
                                                            token_usage=token_usage)
        self.record = save_analysis_answer(session=_session, record_id=self.record.id,
                                           answer=orjson.dumps({'content': full_analysis_text}).decode())

    def generate_predict(self, _session: Session):
        fields = self.get_fields_from_chart(_session)
        self.chat_question.fields = orjson.dumps(fields).decode()
        data = get_chat_chart_data(_session, self.record.id)
        self.chat_question.data = orjson.dumps(data.get('data')).decode()

        ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None
        self.filter_custom_prompts(_session, CustomPromptTypeEnum.PREDICT_DATA, self.current_user.oid, ds_id)

        predict_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        predict_msg.append(SystemMessage(content=self.chat_question.predict_sys_question()))
        predict_msg.append(HumanMessage(content=self.chat_question.predict_user_question()))
        predict_msg = self._with_query_constraints(_session, predict_msg)

        self.current_logs[OperationEnum.PREDICT_DATA] = start_log(session=_session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.PREDICT_DATA,
                                                                  record_id=self.record.id,
                                                                  full_message=[
                                                                      {'type': msg.type,
                                                                       'content': msg.content} for
                                                                      msg
                                                                      in predict_msg])
        full_thinking_text = ''
        full_predict_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(predict_msg, operation=OperationEnum.PREDICT_DATA), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_predict_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        predict_msg.append(AIMessage(full_predict_text))
        self.record = save_predict_answer(session=_session, record_id=self.record.id,
                                          answer=orjson.dumps({'content': full_predict_text}).decode())
        self.current_logs[OperationEnum.PREDICT_DATA] = end_log(session=_session,
                                                                log=self.current_logs[
                                                                    OperationEnum.PREDICT_DATA],
                                                                full_message=[
                                                                    {'type': msg.type,
                                                                     'content': msg.content}
                                                                    for msg in predict_msg],
                                                                reasoning_content=full_thinking_text,
                                                                token_usage=token_usage)

    def generate_recommend_questions_task(self, _session: Session):

        # get schema
        if self.ds and not self.chat_question.db_schema:
            self.chat_question.db_schema = self.out_ds_instance.get_db_schema(
                self.ds.id, self.chat_question.question) if self.out_ds_instance else get_table_schema(
                session=_session,
                current_user=self.current_user, ds=self.ds,
                question=self.chat_question.question,
                embedding=False)

        guess_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        guess_msg.append(SystemMessage(content=self.chat_question.guess_sys_question(self.articles_number)))

        old_questions = list(map(lambda q: q.strip(), get_old_questions(_session, self.record.datasource)))
        guess_msg.append(
            HumanMessage(content=self.chat_question.guess_user_question(orjson.dumps(old_questions).decode())))

        self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = start_log(session=_session,
                                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                                    operate=OperationEnum.GENERATE_RECOMMENDED_QUESTIONS,
                                                                                    record_id=self.record.id,
                                                                                    full_message=[
                                                                                        {'type': msg.type,
                                                                                         'content': msg.content} for
                                                                                        msg
                                                                                        in guess_msg])
        full_thinking_text = ''
        full_guess_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(guess_msg, operation=OperationEnum.GENERATE_RECOMMENDED_QUESTIONS), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_guess_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        guess_msg.append(AIMessage(full_guess_text))

        self.current_logs[OperationEnum.GENERATE_RECOMMENDED_QUESTIONS] = end_log(session=_session,
                                                                                  log=self.current_logs[
                                                                                      OperationEnum.GENERATE_RECOMMENDED_QUESTIONS],
                                                                                  full_message=[
                                                                                      {'type': msg.type,
                                                                                       'content': msg.content}
                                                                                      for msg in guess_msg],
                                                                                  reasoning_content=full_thinking_text,
                                                                                  token_usage=token_usage)
        self.record = save_recommend_question_answer(session=_session, record_id=self.record.id,
                                                     answer={'content': full_guess_text},
                                                     articles_number=self.articles_number)

        yield {'recommended_question': self.record.recommended_question}

    def select_datasource(self, _session: Session):
        datasource_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        datasource_msg.append(SystemMessage(self.chat_question.datasource_sys_question()))
        if self.current_assistant and self.current_assistant.type != 4:
            _ds_list = get_assistant_ds(session=_session, llm_service=self)
        else:
            stmt = select(CoreDatasource.id, CoreDatasource.name, CoreDatasource.description).where(
                and_(CoreDatasource.oid == self.current_user.oid))
            _ds_list = [
                {
                    "id": ds.id,
                    "name": ds.name,
                    "description": ds.description
                }
                for ds in _session.exec(stmt)
            ]
        if not _ds_list:
            # 主线二 Phase 2a：流程图"暂无相关数据表"分支
            # 返回结构化 payload 供前端展示"暂无相关数据表 + 录入需求"引导
            # 保持沿用现有 SingleMessageError→SSE error 的惯例（参见 run_task 异常处理 line 1343）
            raise SingleMessageError(orjson.dumps({
                'type': 'datasource_not_found',
                'reason': 'empty_available_datasource',
                'message': 'No datasource available for current user',
                'candidates': [],
                'actions': [{'type': 'record_question', 'label': '录入需求'}],
            }).decode())
        ignore_auto_select = _ds_list and len(_ds_list) == 1
        # ignore auto select ds

        full_thinking_text = ''
        full_text = ''
        if not ignore_auto_select:
            if settings.TABLE_EMBEDDING_ENABLED and (
                    not self.current_assistant or (self.current_assistant and self.current_assistant.type != 1)):
                _ds_list = get_ds_embedding(_session, self.current_user, _ds_list, self.out_ds_instance,
                                            self.chat_question.question, self.current_assistant)
                # yield {'content': '{"id":' + str(ds.get('id')) + '}'}

            _ds_list_dict = []
            for _ds in _ds_list:
                _ds_list_dict.append(_ds)
            datasource_msg.append(
                HumanMessage(self.chat_question.datasource_user_question(orjson.dumps(_ds_list_dict).decode())))
            datasource_msg = self._with_query_constraints(_session, datasource_msg)

            self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = start_log(session=_session,
                                                                           ai_modal_id=self.chat_question.ai_modal_id,
                                                                           ai_modal_name=self.chat_question.ai_modal_name,
                                                                           operate=OperationEnum.CHOOSE_DATASOURCE,
                                                                           record_id=self.record.id,
                                                                           full_message=[{'type': msg.type,
                                                                                          'content': msg.content}
                                                                                         for
                                                                                         msg in datasource_msg])

            token_usage = {}
            res = process_stream(self._model_stream(datasource_msg, operation=OperationEnum.CHOOSE_DATASOURCE), token_usage)
            for chunk in res:
                if chunk.get('content'):
                    full_text += chunk.get('content')
                if chunk.get('reasoning_content'):
                    full_thinking_text += chunk.get('reasoning_content')
                yield chunk
            datasource_msg.append(AIMessage(full_text))

            self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = end_log(session=_session,
                                                                         log=self.current_logs[
                                                                             OperationEnum.CHOOSE_DATASOURCE],
                                                                         full_message=[
                                                                             {'type': msg.type,
                                                                              'content': msg.content}
                                                                             for msg in datasource_msg],
                                                                         reasoning_content=full_thinking_text,
                                                                         token_usage=token_usage)

            json_str = extract_nested_json(full_text)
            if json_str is None:
                raise SingleMessageError(f'Cannot parse datasource from answer: {full_text}')
            ds = orjson.loads(json_str)

        _error: Exception | None = None
        _datasource: int | None = None
        _engine_type: str | None = None
        try:
            data: dict = _ds_list[0] if ignore_auto_select else ds

            if data.get('id') and data.get('id') != 0:
                _datasource = data['id']
                _chat = _session.get(Chat, self.record.chat_id)
                _chat.datasource = _datasource
                if self.current_assistant and self.current_assistant.type in dynamic_ds_types:
                    _ds = self.out_ds_instance.get_ds(data['id'])
                    self.ds = _ds
                    self.chat_question.engine = _ds.type + get_version(self.ds)

                    _engine_type = self.chat_question.engine
                    _chat.engine_type = _ds.type
                else:
                    _ds = _session.get(CoreDatasource, _datasource)
                    if not _ds:
                        _datasource = None
                        raise SingleMessageError(f"Datasource configuration with id {_datasource} not found")
                    self.ds = CoreDatasource(**_ds.model_dump())
                    self.chat_question.engine = (_ds.type_name if _ds.type != 'excel' else 'PostgreSQL') + get_version(
                        self.ds)

                    _engine_type = self.chat_question.engine
                    _chat.engine_type = _ds.type_name
                # save chat
                with _session.begin_nested():
                    # 为了能继续记日志，先单独处理下事务
                    try:
                        _session.add(_chat)
                        _session.flush()
                        _session.refresh(_chat)
                        _session.commit()
                    except Exception as e:
                        _session.rollback()
                        raise e

            elif data['fail']:
                # 主线二 Phase 2a：流程图"暂无相关数据表"分支（LLM 判定无关联数据源，给出 fail 文案）
                raise SingleMessageError(orjson.dumps({
                    'type': 'datasource_not_found',
                    'reason': 'no_candidate_reach_threshold',
                    'message': data['fail'],
                    'candidates': [{'id': _ds.get('id'), 'name': _ds.get('name')} for _ds in _ds_list],
                    'actions': [{'type': 'record_question', 'label': '录入需求'}],
                }).decode())
            else:
                # 主线二 Phase 2a：LLM 返回 id=0 但未给 fail 的兜底
                raise SingleMessageError(orjson.dumps({
                    'type': 'datasource_not_found',
                    'reason': 'llm_no_choice',
                    'message': 'LLM did not pick any datasource',
                    'candidates': [{'id': _ds.get('id'), 'name': _ds.get('name')} for _ds in _ds_list],
                    'actions': [{'type': 'record_question', 'label': '录入需求'}],
                }).decode())

        except Exception as e:
            _error = e

        if not ignore_auto_select and not settings.TABLE_EMBEDDING_ENABLED:
            self.record = save_select_datasource_answer(session=_session, record_id=self.record.id,
                                                        answer=orjson.dumps({'content': full_text}).decode(),
                                                        datasource=_datasource,
                                                        engine_type=_engine_type)
        if self.ds:
            oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
            ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

            self.filter_terminology_template(_session, oid, ds_id)

            self.filter_training_template(_session, oid, ds_id)

            self.filter_custom_prompts(_session, CustomPromptTypeEnum.GENERATE_SQL, oid, ds_id)

            self.init_messages(_session)

        if _error:
            raise _error

    def _is_platform_virtual_assistant(self) -> bool:
        if not self.current_assistant:
            return False
        configuration = getattr(self.current_assistant, "configuration", None)
        if isinstance(configuration, str):
            try:
                configuration = json.loads(configuration)
            except Exception:
                return False
        return bool(configuration.get("platform_virtual")) if isinstance(configuration, dict) else False

    # 主流程、召回、LLM 路由、SQL 校验、权限 Gate。
    def _logical_datasource_enabled(self) -> bool:
        return bool(
            settings.PLATFORM_DATASOURCE_ENABLED
            and settings.PLATFORM_LOGICAL_DATASOURCE_ENABLED
            and self.current_assistant
            and self.current_assistant.type in dynamic_ds_types
            and self._is_platform_virtual_assistant()
        )

    def _ensure_out_ds_instance(self) -> AssistantOutDs:
        if not self.current_assistant:
            raise SingleMessageError("No assistant available for platform datasource routing")
        if not self.out_ds_instance:
            self.out_ds_instance = AssistantOutDsFactory.get_instance(self.current_assistant)
        return self.out_ds_instance
    # 所有 datasource 下的表拍平成全量表目录，供 LLM 选择
    def _logical_table_entries(self) -> list[dict[str, Any]]:
        out_ds_instance = self._ensure_out_ds_instance()
        entries: list[dict[str, Any]] = []
        for ds in out_ds_instance.ds_list or []:
            physical_key = AssistantOutDs._physical_datasource_key(ds)
            for table in ds.tables or []:
                schema_name = self._table_schema_name(ds, table)
                table_name = str(getattr(table, "name", "") or "")
                table_id = getattr(table, "tableId", None) or getattr(table, "id", None)
                field_preview = []
                for field in (table.fields or []):
                    field_preview.append({
                        "name": field.name,
                        "alias": getattr(field, "alias", None),
                        "type": field.type,
                        "comment": field.comment,
                    })
                entries.append({
                    "datasourceId": ds.id,
                    "datasourceName": ds.name,
                    "datasourceType": ds.type,
                    "physicalDatasourceKey": physical_key,
                    "host": getattr(ds, "host", None),
                    "port": getattr(ds, "port", None),
                    "dataSourceId": getattr(table, "dataSourceId", None) or ds.id,
                    "tableId": table_id,
                    "categoryId": getattr(table, "categoryId", None),
                    "schemaName": schema_name,
                    "name": table_name,
                    "qualifiedName": self._qualified_table_name(schema_name, table_name),
                    "comment": table.comment,
                    "fields": field_preview,
                })
        return entries

    @staticmethod
    def _logical_table_entry_key(entry: dict[str, Any]) -> str:
        return (
            f"{entry.get('datasourceId')}::"
            f"{str(entry.get('schemaName', '')).lower()}::"
            f"{str(entry.get('name', '')).lower()}::"
            f"{entry.get('tableId')}"
        )

    @staticmethod
    def _logical_table_recall_text(entry: dict[str, Any]) -> str:
        parts = [
            entry.get("qualifiedName"),
            entry.get("name"),
            entry.get("comment"),
            entry.get("datasourceName"),
            entry.get("datasourceType"),
            entry.get("schemaName"),
        ]
        metadata = entry.get("_indexed_metadata") or {}
        parts.extend([metadata.get("categoryName"), metadata.get("categoryComment"), metadata.get("comment")])
        for field in [*(entry.get("fields") or []), *metadata.get("fields", [])]:
            if isinstance(field, dict):
                parts.extend([field.get("name"), field.get("alias"), field.get("type"), field.get("comment")])
        return " ".join(str(part) for part in parts if part is not None and str(part).strip() != "")

    @staticmethod
    def _logical_route_terms(question: str) -> set[str]:
        terms: set[str] = set()
        for token in re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+", (question or "").lower()):
            if not token:
                continue
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                terms.add(token)
                max_n = min(4, len(token))
                for n in range(2, max_n + 1):
                    for i in range(0, len(token) - n + 1):
                        terms.add(token[i:i + n])
            else:
                terms.add(token)
                terms.update(part for part in token.split("_") if len(part) >= 2)
        return {term for term in terms if len(term) >= 2}

    # 用问题文本匹配表名、schema.table、表注释、字段名、字段说明。中文会切 2-4 字 ngram，英文按 token/下划线切。
    def _keyword_recall_logical_table_entries(
        self, question: str, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        limit = max(0, int(getattr(settings, "PLATFORM_LOGICAL_ROUTE_KEYWORD_COUNT", 30) or 0))
        if not entries or limit <= 0:
            return []
        question_lower = (question or "").lower()
        terms = self._logical_route_terms(question)
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for idx, entry in enumerate(entries):
            text = self._logical_table_recall_text(entry).lower()
            name = str(entry.get("name") or "").lower()
            qualified_name = str(entry.get("qualifiedName") or "").lower()
            score = 0
            if question_lower and question_lower in text:
                score += 50
            if name and name in question_lower:
                score += 100
            if qualified_name and qualified_name in question_lower:
                score += 120
            for term in terms:
                if term in text:
                    score += len(term)
            if score > 0:
                scored.append((score, idx, entry))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [entry for _, _, entry in scored[:limit]]

    # 只编码问题；物理表向量由数据源页面手动同步。
    def _embedding_recall_logical_table_entries(
        self, question: str, entries: list[dict[str, Any]], limit: int
    ) -> list[dict[str, Any]]:
        if (
            not entries
            or limit <= 0
            or not getattr(settings, "PLATFORM_LOGICAL_ROUTE_EMBEDDING_ENABLED", True)
            or not question
        ):
            return []
        try:
            recalled, self._platform_index_available = get_platform_index().recall(
                int(self.current_user.oid), question, entries, limit)
            return recalled
        except Exception as e:
            SQLBotLogUtil.warning(f"Logical datasource embedding recall failed: {type(e).__name__}")
            return []

    # 始终限制候选数；没有候选时请求澄清或同步索引，不能回退全量目录。
    def _recall_logical_table_entries(
        self, question: str, entries: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        self._platform_index_available = False
        limit = max(1, min(50, int(getattr(settings, "PLATFORM_LOGICAL_ROUTE_RECALL_COUNT", 50) or 50)))
        try:
            entries = get_platform_index().enrich(int(self.current_user.oid), entries)
        except Exception as exc:
            SQLBotLogUtil.warning(f"Platform index metadata unavailable: {type(exc).__name__}")
        keyword_entries = self._keyword_recall_logical_table_entries(question, entries)
        # Exact table identities must not be lost to the keyword branch's smaller limit.
        keyword_entries = [e for e in entries if exact_name(question, e)] + keyword_entries
        embedding_entries = self._embedding_recall_logical_table_entries(question, entries, limit)

        return fuse(question, keyword_entries, embedding_entries, limit)

    def _default_schema_name(self) -> Optional[str]:
        """Schema the execution connection runs an unqualified table name in, or None when SQLBot cannot tell
        (the name then stays unresolved). Mirrors apps/db/db.py: PostgreSQL gets search_path=db_schema only when
        db_schema is set; MySQL-family engines run in the connection database; the other engines keep the login's
        own default, which is what a table labelled with no schema at all (no db_schema, no database) stands for."""
        if not self.ds:
            return None
        ds_type = str(getattr(self.ds, "type", "") or "").lower()
        db_schema = str(getattr(self.ds, "db_schema", "") or "").strip()
        database = str(getattr(self.ds, "dataBase", "") or "").strip()
        if ds_type == "pg":
            return db_schema or None
        if ds_type in ("mysql", "doris", "starrocks", "ck"):
            return database or None
        if ds_type == "sqlserver" and not database:
            # pymssql would connect to the login's default database, not necessarily the one Data catalogued.
            return None
        return "" if not db_schema and not database else None

    @staticmethod
    def _table_schema_name(ds, table) -> str:
        schema_name = getattr(table, "schemaName", None)
        if schema_name is not None and str(schema_name).strip() != "":
            return str(schema_name)
        db_schema = getattr(ds, "db_schema", None)
        if db_schema is not None and str(db_schema).strip() != "":
            return str(db_schema)
        database = getattr(ds, "dataBase", None)
        if database is not None and str(database).strip() != "":
            return str(database)
        return ""

    @staticmethod
    def _qualified_table_name(schema_name: Optional[str], table_name: Optional[str]) -> str:
        name = str(table_name or "")
        schema = str(schema_name or "").strip()
        return f"{schema}.{name}" if schema else name

    @staticmethod
    def _split_table_name(table_name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if not table_name:
            return None, None
        parts = [part.strip(' `"[]') for part in str(table_name).split(".") if part.strip(' `"[]')]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None, parts[0] if parts else None

    @staticmethod
    def _datasource_not_found_payload(reason: str, message: str, candidates: Optional[list[dict[str, Any]]] = None):
        return {
            "type": "datasource_not_found",
            "reason": reason,
            "message": message,
            "candidates": candidates or [],
            "actions": [{"type": "record_question", "label": "录入需求"}],
        }

    @staticmethod
    def _cross_source_payload(tables: list[dict[str, Any]]):
        dedup: dict[str, dict[str, Any]] = {}
        for table in tables:
            key = f"{table.get('datasourceId')}::{str(table.get('schemaName', '')).lower()}::{str(table.get('name', '')).lower()}"
            dedup[key] = {
                "name": table.get("name"),
                "schemaName": table.get("schemaName"),
                "qualifiedName": table.get("qualifiedName"),
                "tableId": table.get("tableId"),
                "categoryId": table.get("categoryId"),
                "datasourceId": table.get("datasourceId"),
                "datasourceName": table.get("datasourceName"),
                "datasourceType": table.get("datasourceType"),
                "physicalDatasourceKey": table.get("physicalDatasourceKey"),
                "host": table.get("host"),
                "port": table.get("port"),
            }
        return {
            "type": "cross_source_not_supported",
            "message": "当前问题涉及多个数据源，暂不支持跨源联合查询，请拆分问题或联系数据管理员建模。",
            "tables": list(dedup.values()),
            "actions": [{"type": "split_question", "label": "拆分问题"}],
        }

    @staticmethod
    def _match_logical_entries(
        entries: list[dict[str, Any]], table_name: Optional[str], datasource_id: Optional[Any] = None,
        schema_name: Optional[str] = None, table_id: Optional[Any] = None
    ) -> list[dict[str, Any]]:
        ds_id = str(datasource_id) if datasource_id is not None and datasource_id != "" else None
        schema = str(schema_name or "").lower()
        if table_id is not None and table_id != "":
            return [
                entry for entry in entries
                if str(entry.get("tableId")) == str(table_id)
                and (ds_id is None or str(entry.get("datasourceId")) == ds_id)
                and (not schema or str(entry.get("schemaName", "")).lower() == schema)
            ]
        if not table_name:
            return []
        parsed_schema, parsed_name = LLMService._split_table_name(table_name)
        name = str(parsed_name or table_name).lower()
        schema = str(schema_name or parsed_schema or "").lower()
        result = []
        for entry in entries:
            if str(entry.get("name", "")).lower() != name:
                continue
            if ds_id is not None and str(entry.get("datasourceId")) != ds_id:
                continue
            if schema and str(entry.get("schemaName", "")).lower() != schema:
                continue
            result.append(entry)
        return result

    def _logical_question_with_history(self, session: Session, entries):
        question = self.chat_question.question
        if any(e.get('datasourceName') and e['datasourceName'] in question for e in entries):
            return question
        logs = getattr(self, 'generate_sql_logs', [])
        if not logs:
            return question
        from apps.chat.task.record_access import require_record_access
        try:
            previous = require_record_access(session, self.current_user, logs[-1].pid,
                                             current_assistant=self.current_assistant)
        except Exception:
            # Revoked/invalid history must never supply a source or model context.
            return question
        names = {e.get('datasourceName') for e in entries
                 if str(e.get('datasourceId')) == str(previous.datasource) and e.get('datasourceName')}
        if not previous.sql or len(names) != 1:
            return question
        return f'当前续问沿用数据源：{next(iter(names))}。上一问题：{previous.question}。当前问题：{question}'

    def route_logical_datasource(self, _session: Session):
        route_started = time.perf_counter()
        yield {"_route_progress": ("metadata", "running")}
        all_entries = self._logical_table_entries()
        SQLBotLogUtil.info(f"Platform record_id={self.record.id} metadata_load_ms={getattr(self.out_ds_instance, 'metadata_load_ms', 0):.1f} flatten_ms={(time.perf_counter() - route_started) * 1000:.1f}")
        yield {"_route_progress": ("metadata", "completed")}
        if not all_entries:
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "no_query_permission",
                "当前没有可查询的数据表，请联系数据管理员授权",
            )).decode())
        route_question = self._logical_question_with_history(_session, all_entries)
        recall_started = time.perf_counter()
        yield {"_route_progress": ("recall", "running")}
        entries = self._recall_logical_table_entries(route_question, all_entries)
        SQLBotLogUtil.info(f"Platform record_id={self.record.id} recall_ms={(time.perf_counter() - recall_started) * 1000:.1f}")
        yield {"_route_progress": ("recall", "completed")}
        identities = lambda items: [(e.get('datasourceId'), e.get('tableId'), e.get('schemaName'), e.get('name')) for e in items]
        SQLBotLogUtil.info(f"Platform record_id={self.record.id} authorized_tables={identities(all_entries)} candidates={identities(entries)}")
        if not entries:
            indexed = getattr(self, "_platform_index_available", False)
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "no_relevant_table" if indexed else "index_not_ready",
                "请补充业务对象、指标或时间范围" if indexed else "暂无可用候选表，请联系管理员同步问数索引",
            )).decode())
        SQLBotLogUtil.info(
            f"Logical datasource route candidates: {len(entries)}/{len(all_entries)}"
        )

        route_msg: List[Union[BaseMessage, dict[str, Any]]] = [
            SystemMessage(content=(
                "你是 SQLBot 的表路由器。你的任务是从给定的候选数据表目录中，"
                "选择与用户问题最相关且足以生成 SQL 的真实物理表。"
                "只返回 JSON，不要输出解释。"
                "若存在强相关表，返回："
                "{\"success\":true,\"tables\":[{\"datasourceId\":数据源ID,\"schemaName\":\"schema\",\"name\":\"表名\",\"tableId\":表ID}]}。"
                "若没有相关表，返回：{\"success\":false,\"message\":\"原因\"}。"
                "可以选择多张表，但不要选择弱相关或仅名称相似的表。"
                "若多个数据源存在同名同义表且用户未明确数据源，不要任选一个，返回success=false并要求明确数据源。"
                "如果目录中提供 schemaName/tableId，必须原样返回。"
            )),
            HumanMessage(content=orjson.dumps({
                "question": route_question,
                "tables": [routing_card(e, self._logical_route_terms(route_question)) for e in entries],
            }).decode()),
        ]

        route_msg = self._with_query_constraints(_session, route_msg)
        yield {"_route_progress": ("selection", "running")}
        self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = start_log(
            session=_session,
            ai_modal_id=self.chat_question.ai_modal_id,
            ai_modal_name=self.chat_question.ai_modal_name,
            operate=OperationEnum.CHOOSE_DATASOURCE,
            record_id=self.record.id,
            full_message=[{"type": msg.type, "content": msg.content} for msg in route_msg],
        )

        full_thinking_text = ''
        full_text = ''
        token_usage = {}
        selection_started = time.perf_counter()
        route_remaining = 30 - (time.perf_counter() - route_started)
        if route_remaining <= 0:
            raise TimeoutError('选表超时，请稍后重试；未开始的查询已取消')
        res = process_stream(self._model_stream(route_msg, route_remaining, operation=OperationEnum.CHOOSE_TABLE), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        SQLBotLogUtil.info(f"Platform record_id={self.record.id} llm_selection_ms={(time.perf_counter() - selection_started) * 1000:.1f} token_usage={token_usage}")
        route_msg.append(AIMessage(full_text))
        self.current_logs[OperationEnum.CHOOSE_DATASOURCE] = end_log(
            session=_session,
            log=self.current_logs[OperationEnum.CHOOSE_DATASOURCE],
            full_message=[{"type": msg.type, "content": msg.content} for msg in route_msg],
            reasoning_content=full_thinking_text,
            token_usage=token_usage,
        )

        json_str = extract_nested_json(full_text)
        if json_str is None:
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "logical_route_parse_failed",
                "Cannot parse table route from answer",
            )).decode())

        try:
            data = orjson.loads(json_str)
        except Exception:
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "logical_route_invalid_json",
                "Cannot parse table route from answer",
            )).decode())

        if not data.get("success"):
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "no_relevant_table",
                data.get("message") or "No relevant table found",
                [{"name": item.get("name"), "datasourceId": item.get("datasourceId")} for item in entries[:20]],
            )).decode())

        selected: list[dict[str, Any]] = []
        for item in data.get("tables") or []:
            if not isinstance(item, dict):
                continue
            table_name = item.get("name") or item.get("tableName")
            datasource_id = item.get("datasourceId") or item.get("datasource_id")
            schema_name = item.get("schemaName") or item.get("schema") or item.get("db_schema")
            table_id = item.get("tableId") or item.get("id")
            matches = self._match_logical_entries(entries, table_name, datasource_id, schema_name, table_id)
            matches = [e for e in matches if datasource_id is not None and table_id is not None
                       and str(table_name or '').lower() in (str(e.get('name', '')).lower(), str(e.get('qualifiedName', '')).lower())]
            if not matches:
                raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                    "invalid_route_selection", "选表结果超出当前候选范围，请补充问题后重试",
                )).decode())
            selected.extend(matches)

        selected_map = {
            f"{item.get('datasourceId')}::{str(item.get('schemaName', '')).lower()}::{str(item.get('name', '')).lower()}": item
            for item in selected
        }
        selected = list(selected_map.values())
        if not selected:
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "no_relevant_table",
                "No relevant table found",
            )).decode())

        physical_keys = {
            item.get("physicalDatasourceKey") or f"datasource:{item.get('datasourceId')}"
            for item in selected
        }
        for item in selected:
            if item.get('datasourceName') and item['datasourceName'] in route_question:
                continue
            duplicates = [entry for entry in all_entries
                if entry.get('qualifiedName') == item.get('qualifiedName')
                and entry.get('physicalDatasourceKey') != item.get('physicalDatasourceKey')]
            if duplicates:
                raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                    'ambiguous_datasource', '多个可查询数据源存在同名表，请在问题中明确数据源名称后重试。',
                    [{'name': entry.get('datasourceName')} for entry in [item, *duplicates]],
                )).decode())
        if len(physical_keys) > 1:
            raise SingleMessageError(orjson.dumps(self._cross_source_payload(selected)).decode())

        physical_key = next(iter(physical_keys))
        datasource_id = selected[0].get("datasourceId")
        ds = self._ensure_out_ds_instance().get_ds(datasource_id)
        self.ds = ds
        self.chat_question.engine = ds.type + get_version(self.ds)
        self.logical_route_tables = selected
        self.logical_route_physical_datasource_key = physical_key
        self.logical_route_table_names = [str(item.get("qualifiedName") or item.get("name")) for item in selected if item.get("name")]
        self.logical_route_table_refs = [
            {
                "name": item.get("name"),
                "schemaName": item.get("schemaName"),
                "tableId": item.get("tableId"),
                "categoryId": item.get("categoryId"),
                "datasourceId": item.get("datasourceId"),
            }
            for item in selected
        ]
        yield {"_route_progress": ("selection", "completed")}

        self.record = save_select_datasource_answer(
            session=_session,
            record_id=self.record.id,
            answer=orjson.dumps({"content": full_text}).decode(),
            datasource=ds.id,
            engine_type=self.chat_question.engine,
        )

        oid = 1
        self.filter_terminology_template(_session, oid, None)
        self.filter_training_template(_session, oid, None)
        self.filter_custom_prompts(_session, CustomPromptTypeEnum.GENERATE_SQL, oid, None)
        self.init_messages(_session)

    def _current_datasource_entries(self) -> list[dict[str, Any]]:
        if not self.ds:
            return []
        ds_list = [self.ds]
        route_physical_key = self.logical_route_physical_datasource_key
        if self._logical_datasource_enabled() and route_physical_key and self.out_ds_instance:
            ds_list = self.out_ds_instance.get_physical_group_ds(self.ds, route_physical_key)
        entries: list[dict[str, Any]] = []
        for ds_item in ds_list:
            physical_key = AssistantOutDs._physical_datasource_key(ds_item)
            for table in (getattr(ds_item, "tables", None) or []):
                table_name = str(getattr(table, "name", "") or "")
                schema_name = self._table_schema_name(ds_item, table)
                table_id = getattr(table, "tableId", None) or getattr(table, "id", None)
                entries.append({
                    "datasourceId": getattr(ds_item, "id", None),
                    "datasourceName": getattr(ds_item, "name", None),
                    "datasourceType": getattr(ds_item, "type", None),
                    "physicalDatasourceKey": physical_key,
                    "host": getattr(ds_item, "host", None),
                    "port": getattr(ds_item, "port", None),
                    "schemaName": schema_name,
                    "name": table_name,
                    "qualifiedName": self._qualified_table_name(schema_name, table_name),
                    "tableId": table_id,
                    "categoryId": getattr(table, "categoryId", None),
                })
        return entries

    def _normalize_table_input(self, item: Any) -> dict[str, Any]:
        if isinstance(item, SQLTableRef):
            return {"name": item.name, "schemaName": item.schema, "catalog": item.catalog}
        if isinstance(item, dict):
            return {
                "name": item.get("name") or item.get("tableName"),
                "schemaName": item.get("schemaName") or item.get("schema") or item.get("db_schema"),
                "tableId": item.get("tableId") or item.get("id"),
                "categoryId": item.get("categoryId"),
                "datasourceId": item.get("datasourceId") or item.get("datasource_id"),
            }
        schema_name, table_name = self._split_table_name(str(item)) if item is not None else (None, None)
        return {"name": table_name, "schemaName": schema_name}

    @staticmethod
    def _permission_ref_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": entry.get("name"),
            "schemaName": entry.get("schemaName"),
            "tableId": entry.get("tableId"),
            "categoryId": entry.get("categoryId"),
        }

    def _resolve_table_inputs(
        self, inputs: list[Any]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        current_entries = self._current_datasource_entries()
        route_entries = self.logical_route_tables or []
        resolved: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []

        for item in inputs:
            ref = self._normalize_table_input(item)
            if ref.get('catalog') and ref['catalog'] != getattr(self.ds, 'dataBase', None):
                unresolved.append({**ref, 'reason': 'cross_database_reference'})
                continue
            table_id = ref.get("tableId")
            table_name = ref.get("name")
            schema_name = ref.get("schemaName")
            datasource_id = ref.get("datasourceId")

            matches: list[dict[str, Any]] = []
            schema = str(schema_name or "").lower()
            ds_id = str(datasource_id) if datasource_id is not None and datasource_id != "" else None
            if table_id is not None and table_id != "":
                matches = [
                    entry for entry in current_entries
                    if str(entry.get("tableId")) == str(table_id)
                    and (not schema or str(entry.get("schemaName", "")).lower() == schema)
                    and (ds_id is None or str(entry.get("datasourceId")) == ds_id)
                ]

            if table_id is None and not matches and table_name:
                name = str(table_name).lower()
                matches = [
                    entry for entry in current_entries
                    if str(entry.get("name", "")).lower() == name
                    and (not schema or str(entry.get("schemaName", "")).lower() == schema)
                    and (ds_id is None or str(entry.get("datasourceId")) == ds_id)
                ]
                if not schema and matches:
                    # The connection runs an unqualified name in its default schema, so only a table there
                    # can vouch for it; a same-named granted table in another schema must not.
                    default_schema = self._default_schema_name()
                    matches = [entry for entry in matches if default_schema is not None
                               and str(entry.get("schemaName") or "").lower() == default_schema.lower()]
                    if not matches:
                        unresolved.append({**ref, "reason": "unqualified_table_outside_default_schema"})
                        continue

            if len(matches) > 1 and not schema_name:
                routed = {(str(entry.get("datasourceId")), str(entry.get("tableId"))) for entry in route_entries}
                route_matches = [
                    entry for entry in matches
                    if (str(entry.get("datasourceId")), str(entry.get("tableId"))) in routed
                ]
                if len(route_matches) == 1:
                    matches = route_matches

            if len(matches) == 1:
                resolved.append(matches[0])
            elif len(matches) > 1:
                unresolved.append({**ref, "reason": "ambiguous_table_reference"})
            else:
                unresolved.append({**ref, "reason": "table_not_in_current_datasource"})

        dedup: dict[str, dict[str, Any]] = {}
        for entry in resolved:
            key = f"{entry.get('datasourceId')}::{entry.get('schemaName')}::{str(entry.get('name', '')).lower()}::{entry.get('tableId')}"
            dedup[key] = entry
        return list(dedup.values()), unresolved

    def _validate_logical_sql_route(self, sql: str, llm_tables: Optional[list]):
        if not self._logical_datasource_enabled() or not self.ds:
            return
        dialect = getattr(self.ds, "type", None) or None
        parser_refs = extract_table_refs(sql, dialect=dialect)
        if parser_refs is None:
            raise SingleMessageError(orjson.dumps({
                "type": "cross_source_not_supported",
                "reason": "sql_parse_failed",
                "message": "无法解析生成的 SQL，已阻止执行以避免跨源或越权查询。",
                "tables": [],
                "actions": [{"type": "split_question", "label": "拆分问题"}],
            }).decode())

        inputs = list(parser_refs)
        if not inputs and llm_tables:
            inputs.extend(llm_tables)
        if not inputs:
            return

        resolved, unresolved = self._resolve_table_inputs(inputs)
        if self.logical_route_tables:
            allowed = {(str(e.get('datasourceId')), str(e.get('tableId'))) for e in self.logical_route_tables}
            unresolved.extend(dict(e, reason='table_outside_selected_route') for e in resolved
                              if (str(e.get('datasourceId')), str(e.get('tableId'))) not in allowed)
        if not unresolved:
            return
        unqualified = [item for item in unresolved if item.get("reason") == "unqualified_table_outside_default_schema"]
        if unqualified:
            # Not a cross-source question: the table exists here, only the schema is missing in the SQL.
            raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
                "unqualified_table_outside_default_schema",
                "生成的 SQL 里有表名没写 schema，而这张表不在数据源默认 schema 中，已拒绝执行。请在问题中写明表的完整名称（schema.表名）后重试。",
                unqualified,
            )).decode())

        entries = self._logical_table_entries()
        matched_other: list[dict[str, Any]] = []
        current_physical_key = self.logical_route_physical_datasource_key or AssistantOutDs._physical_datasource_key(self.ds)
        for item in unresolved:
            matches = self._match_logical_entries(
                entries,
                item.get("name"),
                schema_name=item.get("schemaName"),
                table_id=item.get("tableId"),
            )
            matched_other.extend([
                match for match in matches
                if str(match.get("physicalDatasourceKey") or f"datasource:{match.get('datasourceId')}") != str(current_physical_key)
            ])
        if matched_other:
            raise SingleMessageError(orjson.dumps(self._cross_source_payload(resolved + matched_other)).decode())

        raise SingleMessageError(orjson.dumps(self._datasource_not_found_payload(
            "generated_table_not_in_current_datasource",
            "Generated SQL contains tables outside routed datasource",
            unresolved,
        )).decode())

    def generate_sql(self, _session: Session, report_progress: bool = False):
        # append current question
        self.sql_message.append(HumanMessage(
            self.chat_question.sql_user_question(current_time=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                                                 change_title=self.change_title)))
        self.sql_message = self._with_query_constraints(_session, self.sql_message)

        self.current_logs[OperationEnum.GENERATE_SQL] = start_log(session=_session,
                                                                  ai_modal_id=self.chat_question.ai_modal_id,
                                                                  ai_modal_name=self.chat_question.ai_modal_name,
                                                                  operate=OperationEnum.GENERATE_SQL,
                                                                  record_id=self.record.id,
                                                                  full_message=[
                                                                      {'type': msg.type, 'content': msg.content} for msg
                                                                      in self.sql_message])
        full_thinking_text = ''
        full_sql_text = ''
        token_usage = {}
        if report_progress:
            yield {"_progress": "waiting_model"}
        res = process_stream(self._model_stream(self.sql_message, operation=OperationEnum.GENERATE_SQL), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_sql_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        self.sql_message.append(AIMessage(full_sql_text))

        self.current_logs[OperationEnum.GENERATE_SQL] = end_log(session=_session,
                                                                log=self.current_logs[OperationEnum.GENERATE_SQL],
                                                                full_message=[{'type': msg.type, 'content': msg.content}
                                                                              for msg in self.sql_message],
                                                                reasoning_content=full_thinking_text,
                                                                token_usage=token_usage)
        self.record = save_sql_answer(session=_session, record_id=self.record.id,
                                      answer=orjson.dumps({'content': full_sql_text}).decode())

    def generate_with_sub_sql(self, session: Session, sql, sub_mappings: list):
        sub_query = json.dumps(sub_mappings, ensure_ascii=False)
        self.chat_question.sql = sql
        self.chat_question.sub_query = sub_query
        dynamic_sql_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        dynamic_sql_msg.append(SystemMessage(content=self.chat_question.dynamic_sys_question()))
        dynamic_sql_msg.append(HumanMessage(content=self.chat_question.dynamic_user_question()))

        self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = start_log(session=session,
                                                                          ai_modal_id=self.chat_question.ai_modal_id,
                                                                          ai_modal_name=self.chat_question.ai_modal_name,
                                                                          operate=OperationEnum.GENERATE_DYNAMIC_SQL,
                                                                          record_id=self.record.id,
                                                                          full_message=[{'type': msg.type,
                                                                                         'content': msg.content}
                                                                                        for
                                                                                        msg in dynamic_sql_msg])

        full_thinking_text = ''
        full_dynamic_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(dynamic_sql_msg, operation=OperationEnum.GENERATE_DYNAMIC_SQL), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_dynamic_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')

        dynamic_sql_msg.append(AIMessage(full_dynamic_text))

        self.current_logs[OperationEnum.GENERATE_DYNAMIC_SQL] = end_log(session=session,
                                                                        log=self.current_logs[
                                                                            OperationEnum.GENERATE_DYNAMIC_SQL],
                                                                        full_message=[
                                                                            {'type': msg.type,
                                                                             'content': msg.content}
                                                                            for msg in dynamic_sql_msg],
                                                                        reasoning_content=full_thinking_text,
                                                                        token_usage=token_usage)

        SQLBotLogUtil.info(full_dynamic_text)
        return full_dynamic_text

    def generate_assistant_dynamic_sql(self, _session: Session, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        sub_query = []
        result_dict = {}
        for table in ds.tables:
            if table.name in tables and table.sql:
                # sub_query.append({"table": table.name, "query": table.sql})
                result_dict[table.name] = table.sql
                sub_query.append({"table": table.name, "query": f'{dynamic_subsql_prefix}{table.name}'})
        if not sub_query:
            return None
        temp_sql_text = self.generate_with_sub_sql(session=_session, sql=sql, sub_mappings=sub_query)
        result_dict['sqlbot_temp_sql_text'] = temp_sql_text
        return result_dict

    def build_table_filter(self, session: Session, sql: str, filters: list):
        filter = json.dumps(filters, ensure_ascii=False)
        self.chat_question.sql = sql
        self.chat_question.filter = filter
        permission_sql_msg: List[Union[BaseMessage, dict[str, Any]]] = []
        permission_sql_msg.append(SystemMessage(content=self.chat_question.filter_sys_question()))
        permission_sql_msg.append(HumanMessage(content=self.chat_question.filter_user_question()))

        self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = start_log(session=session,
                                                                                   ai_modal_id=self.chat_question.ai_modal_id,
                                                                                   ai_modal_name=self.chat_question.ai_modal_name,
                                                                                   operate=OperationEnum.GENERATE_SQL_WITH_PERMISSIONS,
                                                                                   record_id=self.record.id,
                                                                                   full_message=[
                                                                                       {'type': msg.type,
                                                                                        'content': msg.content} for
                                                                                       msg
                                                                                       in permission_sql_msg])
        full_thinking_text = ''
        full_filter_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(permission_sql_msg, operation=OperationEnum.GENERATE_SQL_WITH_PERMISSIONS), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_filter_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')

        permission_sql_msg.append(AIMessage(full_filter_text))

        self.current_logs[OperationEnum.GENERATE_SQL_WITH_PERMISSIONS] = end_log(session=session,
                                                                                 log=self.current_logs[
                                                                                     OperationEnum.GENERATE_SQL_WITH_PERMISSIONS],
                                                                                 full_message=[
                                                                                     {'type': msg.type,
                                                                                      'content': msg.content}
                                                                                     for msg in permission_sql_msg],
                                                                                 reasoning_content=full_thinking_text,
                                                                                 token_usage=token_usage)

        SQLBotLogUtil.info(full_filter_text)
        return full_filter_text

    def generate_filter(self, _session: Session, sql: str, tables: List):
        filters = get_row_permission_filters(session=_session, current_user=self.current_user, ds=self.ds,
                                             tables=tables)
        if not filters:
            return None
        return self.build_table_filter(session=_session, sql=sql, filters=filters)

    def generate_assistant_filter(self, _session: Session, sql, tables: List):
        ds: AssistantOutDsSchema = self.ds
        filters = []
        for table in ds.tables:
            if table.name in tables and table.rule:
                filters.append({"table": table.name, "filter": table.rule})
        if not filters:
            return None
        return self.build_table_filter(session=_session, sql=sql, filters=filters)

    def generate_chart(self, _session: Session, chart_type: Optional[str] = '', schema: Optional[str] = ''):
        # append current question
        self.chart_message.append(HumanMessage(self.chat_question.chart_user_question(chart_type, schema)))

        self.current_logs[OperationEnum.GENERATE_CHART] = start_log(session=_session,
                                                                    ai_modal_id=self.chat_question.ai_modal_id,
                                                                    ai_modal_name=self.chat_question.ai_modal_name,
                                                                    operate=OperationEnum.GENERATE_CHART,
                                                                    record_id=self.record.id,
                                                                    full_message=[
                                                                        {'type': msg.type, 'content': msg.content} for
                                                                        msg
                                                                        in self.chart_message])
        full_thinking_text = ''
        full_chart_text = ''
        token_usage = {}
        res = process_stream(self._model_stream(self.chart_message, operation=OperationEnum.GENERATE_CHART), token_usage)
        for chunk in res:
            if chunk.get('content'):
                full_chart_text += chunk.get('content')
            if chunk.get('reasoning_content'):
                full_thinking_text += chunk.get('reasoning_content')
            yield chunk

        self.chart_message.append(AIMessage(full_chart_text))

        self.record = save_chart_answer(session=_session, record_id=self.record.id,
                                        answer=orjson.dumps({'content': full_chart_text}).decode())
        self.current_logs[OperationEnum.GENERATE_CHART] = end_log(session=_session,
                                                                  log=self.current_logs[OperationEnum.GENERATE_CHART],
                                                                  full_message=[
                                                                      {'type': msg.type, 'content': msg.content}
                                                                      for msg in self.chart_message],
                                                                  reasoning_content=full_thinking_text,
                                                                  token_usage=token_usage)

    def check_sql(self, session: Session, res: str, operate: OperationEnum) -> tuple[str, Optional[list]]:
        json_str = extract_nested_json(res)

        log = self.current_logs[operate]

        if json_str is None:
            trigger_log_error(session, log)
            raise SingleMessageError(orjson.dumps({'message': 'SQL answer is not a valid json object',
                                                   'traceback': "SQL answer is not a valid json object:\n" + res}).decode())
        sql: str
        data: dict
        try:
            data = orjson.loads(json_str)

            if data['success']:
                sql = data['sql']
            else:
                message = data['message']
                raise SingleMessageError(message)
        except SingleMessageError as e:
            trigger_log_error(session, log)
            raise e
        except Exception:
            trigger_log_error(session, log)
            raise SingleMessageError(orjson.dumps({'message': 'Cannot parse sql from answer',
                                                   'traceback': "Cannot parse sql from answer:\n" + res}).decode())

        if sql.strip() == '':
            trigger_log_error(session, log)
            raise SingleMessageError("SQL query is empty")
        return sql, data.get('tables')

    @staticmethod
    def get_chart_type_from_sql_answer(res: str) -> Optional[str]:
        json_str = extract_nested_json(res)
        if json_str is None:
            return None

        chart_type: Optional[str]
        data: dict
        try:
            data = orjson.loads(json_str)

            if data['success']:
                chart_type = data['chart-type']
            else:
                return None
        except Exception:
            return None

        return chart_type

    @staticmethod
    def get_brief_from_sql_answer(res: str) -> Optional[str]:
        json_str = extract_nested_json(res)
        if json_str is None:
            return None

        brief: Optional[str]
        data: dict
        try:
            data = orjson.loads(json_str)

            if data['success']:
                brief = data['brief']
            else:
                return None
        except Exception:
            return None

        return brief

    def check_save_sql(self, session: Session, res: str, operate: OperationEnum) -> str:
        sql, *_ = self.check_sql(session=session, res=res, operate=operate)
        save_sql(session=session, sql=sql, record_id=self.record.id)

        self.chat_question.sql = sql

        return sql

    def check_save_chart(self, session: Session, res: str) -> Dict[str, Any]:
        chart = self.parse_chart(res)
        save_chart(session=session, chart=orjson.dumps(chart).decode(), record_id=self.record.id)
        return chart

    @staticmethod
    def parse_chart(res: str, result_fields: Optional[list[str]] = None) -> Dict[str, Any]:
        json_str = extract_nested_json(res)
        if json_str is None:
            raise SingleMessageError(orjson.dumps({'message': 'Cannot parse chart config from answer',
                                                   'traceback': "Cannot parse chart config from answer:\n" + res}).decode())
        data: dict

        chart: Dict[str, Any] = {}
        message = ''
        error = False

        try:
            data = orjson.loads(json_str)
            if data['type'] and data['type'] != 'error':
                # todo type check
                chart = data
                if result_fields is not None:
                    return LLMService.validate_result_chart(chart, result_fields)
                if chart.get('columns'):
                    for v in chart.get('columns'):
                        v['value'] = v.get('value').lower()
                if chart.get('axis'):
                    if chart.get('axis').get('x'):
                        chart.get('axis').get('x')['value'] = chart.get('axis').get('x').get('value').lower()
                    y_axis = chart.get('axis').get('y')
                    if y_axis:
                        if isinstance(y_axis, list):
                            # 数组格式: y: [{name, value}, ...]
                            for item in y_axis:
                                if item.get('value'):
                                    item['value'] = item['value'].lower()
                        elif isinstance(y_axis, dict) and y_axis.get('value'):
                            # 旧格式: y: {name, value}
                            y_axis['value'] = y_axis['value'].lower()
                    if chart.get('axis').get('series'):
                        chart.get('axis').get('series')['value'] = chart.get('axis').get('series').get('value').lower()
                if chart.get('axis') and chart['axis'].get('multi-quota'):
                    multi_quota = chart['axis']['multi-quota']
                    if multi_quota.get('value'):
                        if isinstance(multi_quota['value'], list):
                            # 将数组中的每个值转换为小写
                            multi_quota['value'] = [v.lower() if v else v for v in multi_quota['value']]
                        elif isinstance(multi_quota['value'], str):
                            # 如果是字符串，也转换为小写
                            multi_quota['value'] = multi_quota['value'].lower()
            elif data['type'] == 'error':
                message = data['reason']
                error = True
            else:
                raise Exception('Chart is empty')
        except Exception:
            error = True
            message = orjson.dumps({'message': 'Cannot parse chart config from answer',
                                    'traceback': "Cannot parse chart config from answer:\n" + res}).decode()

        if error:
            raise SingleMessageError(message)

        return chart

    @staticmethod
    def rule_table_chart(result: dict, status: str) -> dict:
        return {'type': 'table', 'title': '查询结果',
                'columns': [{'name': field, 'value': field} for field in result.get('fields') or []],
                'render_status': status}

    @staticmethod
    def validate_result_chart(chart: dict, fields: list[str]) -> dict:
        """A generated view must not replace the usable table with invalid field bindings."""
        chart_type = chart.get('type')
        if chart_type == 'table':
            refs = chart.get('columns')
        elif chart_type in {'column', 'bar', 'line', 'pie'}:
            axis = chart.get('axis') or {}
            y = axis.get('y')
            y_refs = y if isinstance(y, list) else [y]
            if not y_refs:
                raise ValueError('Chart values are missing')
            refs = [axis.get('series') if chart_type == 'pie' else axis.get('x'), *y_refs]
            if chart_type != 'pie' and axis.get('series'):
                refs.append(axis['series'])
        else:
            raise ValueError('Unsupported chart type')
        if not isinstance(refs, list) or not refs:
            raise ValueError('Chart fields are missing')
        for ref in refs:
            if not isinstance(ref, dict) or not isinstance(ref.get('value'), str):
                raise ValueError('Invalid chart field')
            value = ref['value']
            matches = [field for field in fields if field.lower() == value.lower()]
            if value in fields:
                actual = value
            elif len(matches) == 1:
                actual = matches[0]
            else:
                raise ValueError('Chart field is not in the query result')
            ref['value'] = actual
            if not isinstance(ref.get('name'), str) or not ref['name']:
                ref['name'] = actual
        return chart

    @staticmethod
    def skip_chart_model(result: dict, chart_type: Optional[str], question: str) -> bool:
        rows, fields = result.get('data'), result.get('fields') or []
        if isinstance(rows, list) and not rows:
            return True
        visual_question = re.sub(
            r'(?:不要|无需|不需要|不用)\s*(?:(?:使用|生成|绘制|展示|显示|用|画)\s*)?(?:任何\s*)?'
            r'(?:柱状图|柱形图|柱图|条形图|折线图|曲线图|饼图|环形图|趋势图|散点图|统计图|分布图|图表|可视化|绘图|图)'
            r"|\b(?:no|without|do not|don't)\s+(?:(?:use|generate|create|show|draw)\s+)?"
            r'(?:(?:any|a|an)\s+)?(?:(?:bar|column|line|pie|scatter)\s+)?'
            r'(?:charts?|plots?|graphs?|visuali[sz]ations?)\b',
            ' ', question or '', flags=re.IGNORECASE)
        # Explicit visual intent wins over a suggested table or a one-cell result.
        if chart_type in {'column', 'bar', 'line', 'pie'} or re.search(
                r'图表|可视化|柱状|柱形|柱图|条形|折线|曲线|饼图|环形|趋势|占比|'
                r'绘图|统计图|分布图|散点图|(?:画|绘制|生成|制作|做).{0,6}图|'
                r'\b(?:charts?|plots?|graphs?|visuali[sz](?:e|ation))\b', visual_question, re.IGNORECASE):
            return False
        single_cell = (isinstance(rows, list) and len(rows) == 1 and len(fields) == 1
                       and isinstance(rows[0], dict) and len(rows[0]) == 1)
        return chart_type == 'table' or single_cell

    def check_save_predict_data(self, session: Session, res: str) -> bool:

        json_str = extract_nested_json(res)

        if not json_str:
            json_str = ''

        save_predict_data(session=session, record_id=self.record.id, data=json_str)

        if json_str == '':
            return False

        return True

    def save_error(self, session: Session, message: str):
        return save_error_message(session=session, record_id=self.record.id, message=message)

    def save_sql_data(self, session: Session, data_obj: Dict[str, Any]):
        try:
            data_result = data_obj.get('data')
            limit = 1000
            if data_result:
                data_result = prepare_for_orjson(data_result)
                if data_result and len(data_result) > limit and self.enable_sql_row_limit:
                    data_obj['data'] = data_result[:limit]
                    data_obj['limit'] = limit
                else:
                    data_obj['data'] = data_result
                data_obj['datasource'] = self.ds.id
            return save_sql_exec_data(session=session, record_id=self.record.id,
                                      data=orjson.dumps(data_obj).decode())
        except Exception as e:
            raise e

    def finish(self, session: Session):
        return finish_record(session=session, record_id=self.record.id)

    def execute_sql(self, sql: str):
        """Execute SQL query

        Args:
            ds: Data source instance
            sql: SQL query statement

        Returns:
            Query results
        """
        self._check_deadline()
        require_read_query(sql)
        started = time.perf_counter()
        SQLBotLogUtil.info(f"Platform record_id={self.record.id} execution_begin ds_id={self.ds.id}")
        try:
            result = exec_sql(ds=self.ds, sql=sql, origin_column=False,
                              deadline=getattr(self, 'query_deadline', None))
            capture = getattr(self, 'operations_capture', None)
            if capture and isinstance(result, dict):
                capture.query_result(result.get('data'))
            return result
        except Exception as e:
            if isinstance(e, ParseSQLResultError):
                raise e
            else:
                err = traceback.format_exc(limit=1, chain=True)
                raise SQLBotDBError(err)
        finally:
            SQLBotLogUtil.info(f"Platform record_id={self.record.id} execution_end elapsed_ms={(time.perf_counter()-started)*1000:.1f}")

    def _check_deadline(self):
        if time.monotonic() >= getattr(self, 'query_deadline', float('inf')):
            raise TimeoutError("问数超过120秒，后续查询已取消")

    def _model_stream(self, messages, limit=120, *, operation: OperationEnum):
        self._check_deadline()
        deadline = min(getattr(self, 'query_deadline', time.monotonic()+120), time.monotonic()+limit)
        # A model-only producer may outlive the consumer briefly; no database
        # continuation runs there, and read timeout bounds its lifetime.
        record_id = self.record.id
        started = time.perf_counter()
        first_output_ms = None
        chunks = 0
        usage = {}
        status, error = 'cancelled', None
        stream = None
        SQLBotLogUtil.info(f"Model record_id={record_id} operation={operation.name} stream_start")
        try:
            model = self.llm.model_copy(update={'request_timeout': min(30, limit), 'max_retries': 0})
            stream = bounded_stream(lambda: model.stream(messages), deadline)
            for chunk in stream:
                chunks += 1
                # Empty role/usage chunks are not the model's first textual output.
                if first_output_ms is None and (chunk.content or chunk.additional_kwargs.get('reasoning_content')):
                    first_output_ms = round((time.perf_counter() - started) * 1000, 1)
                get_token_usage(chunk, usage)
                yield chunk
            status = 'completed'
        except Exception as exc:
            status = 'timeout' if isinstance(exc, (TimeoutError, APITimeoutError)) else 'error'
            error = type(exc).__name__
            raise
        finally:
            if stream is not None and hasattr(stream, 'close'):
                stream.close()
            capture = getattr(self, 'operations_capture', None)
            if capture:
                capture.model(operation.name, (time.perf_counter() - started) * 1000,
                              first_output_ms, usage, status, error)
            SQLBotLogUtil.info(
                f"Model record_id={record_id} operation={operation.name} stream_end status={status} "
                f"first_output_ms={first_output_ms} elapsed_ms={(time.perf_counter() - started) * 1000:.1f} "
                f"chunks={chunks} input_tokens={usage.get('input_tokens')} "
                f"output_tokens={usage.get('output_tokens')} total_tokens={usage.get('total_tokens')} error={error}")

    def pop_chunk(self):
        try:
            chunk = self.chunk_list.pop(0)
            return chunk
        except IndexError as e:
            return None

    def await_result(self):
        while self.is_running():
            while True:
                chunk = self.pop_chunk()
                if chunk is not None:
                    yield chunk
                else:
                    break
        while True:
            chunk = self.pop_chunk()
            if chunk is None:
                break
            yield chunk

    def run_task_async(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        if in_chat:
            stream = True
        self.future = executor.submit(self.run_task_cache, in_chat, stream, finish_step)

    def run_task_cache(self, in_chat: bool = True, stream: bool = True,
                       finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        from apps.operations.capture import run_captured
        run_captured(self, self.run_task(in_chat, stream, finish_step))

    def run_task(self, in_chat: bool = True, stream: bool = True,
                 finish_step: ChatFinishStep = ChatFinishStep.GENERATE_CHART):
        json_result: Dict[str, Any] = {'success': True}
        capture = getattr(self, 'operations_capture', None)
        progress = ChatProgress(in_chat, observer=capture.observe if capture else None)
        _session = None
        try:
            yield from progress.update('route', detail='preparing')
            _session = session_maker()
            if self.ds:
                oid = self.ds.oid if isinstance(self.ds, CoreDatasource) else 1
                ds_id = self.ds.id if isinstance(self.ds, CoreDatasource) else None

                self.filter_terminology_template(_session, oid, ds_id)

                self.filter_training_template(_session, oid, ds_id)

                self.filter_custom_prompts(_session, CustomPromptTypeEnum.GENERATE_SQL, oid, ds_id)

                self.init_messages(_session)

            # return id
            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'id', 'id': self.get_record().id}).decode() + '\n\n'
                if self.get_record().regenerate_record_id:
                    yield 'data:' + orjson.dumps({'type': 'regenerate_record_id',
                                                  'regenerate_record_id': self.get_record().regenerate_record_id}).decode() + '\n\n'
                yield 'data:' + orjson.dumps(
                    {'type': 'question', 'question': self.get_record().question}).decode() + '\n\n'
            else:
                if stream:
                    yield '> ' + self.trans('i18n_chat.record_id_in_mcp') + str(self.get_record().id) + '\n'
                    yield '> ' + self.get_record().question + '\n\n'
            if not stream:
                json_result['record_id'] = self.get_record().id

            if is_greeting(self.chat_question.question):
                if capture:
                    capture.guidance = True
                message = "你好！请输入需要查询的业务对象、指标和时间范围，例如：统计订单总金额。"
                save_select_datasource_answer(_session, self.record.id, message)
                for stage in ('route', 'connect', 'generate', 'permission', 'query', 'result'):
                    yield from progress.update(stage, 'skipped', 'greeting')
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'guidance', 'content': message}).decode() + '\n\n'
                    yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                else:
                    yield {'success': True, 'message': message, 'record_id': self.record.id}
                return

                # select datasource if datasource is none
            if not self.ds:
                logical_ds_mode = self._logical_datasource_enabled()
                ds_res = self.route_logical_datasource(_session) if logical_ds_mode else self.select_datasource(_session)

                for chunk in ds_res:
                    if chunk.get('_route_progress'):
                        detail_stage, detail_status = chunk['_route_progress']
                        yield from progress.update_detail('route', detail_stage, detail_status)
                        continue
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'datasource-result'}).decode() + '\n\n'
                if in_chat and not logical_ds_mode:
                    yield 'data:' + orjson.dumps({'id': self.ds.id, 'datasource_name': self.ds.name,
                                                  'engine_type': self.ds.type_name or self.ds.type,
                                                  'type': 'datasource'}).decode() + '\n\n'

                yield from progress.update('route', 'completed')
            else:
                self.validate_history_ds(_session)
                yield from progress.update('route', 'skipped', 'reuse_datasource')

            # check connection
            yield from progress.update('connect')
            connected = check_connection(ds=self.ds, trans=None)
            if not connected:
                raise SQLBotDBConnectionError('Connect DB failed')
            yield from progress.update('connect', 'completed')

            # generate sql
            yield from progress.update('generate', detail='preparing_context')
            sql_res = self.generate_sql(_session, report_progress=in_chat)
            model_output_started = False
            full_sql_text = ''
            for chunk in sql_res:
                if chunk.get('_progress'):
                    yield from progress.update('generate', detail=chunk['_progress'])
                    continue
                if not model_output_started and (chunk.get('content') or chunk.get('reasoning_content')):
                    model_output_started = True
                    yield from progress.update('generate', detail='receiving_model')
                full_sql_text += chunk.get('content') or ''
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'sql-result'}).decode() + '\n\n'
            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'info', 'msg': 'sql generated'}).decode() + '\n\n'
            yield from progress.update('generate', 'completed')
            yield from progress.update('permission')
            # filter sql
            SQLBotLogUtil.info(full_sql_text)

            chart_type = self.get_chart_type_from_sql_answer(full_sql_text)

            # return title
            if self.change_title:
                llm_brief = self.get_brief_from_sql_answer(full_sql_text)
                llm_brief_generated = bool(llm_brief)
                if llm_brief_generated or (self.chat_question.question and self.chat_question.question.strip() != ''):
                    save_brief = llm_brief if (llm_brief and llm_brief != '') else self.chat_question.question.strip()[
                                                                                   :20]
                    brief = rename_chat(session=_session,
                                        rename_object=RenameChat(id=self.get_record().chat_id,
                                                                 brief=save_brief, brief_generate=llm_brief_generated))
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'brief', 'brief': brief}).decode() + '\n\n'
                    if not stream:
                        json_result['title'] = brief

            use_dynamic_ds: bool = self.current_assistant and self.current_assistant.type in dynamic_ds_types
            is_page_embedded: bool = self.current_assistant and self.current_assistant.type == 4
            dynamic_sql_result = None
            sqlbot_temp_sql_text = None
            assistant_dynamic_sql = None
            # row permission

            sql_operate = OperationEnum.GENERATE_SQL
            sql, tables = self.check_sql(session=_session, res=full_sql_text, operate=sql_operate)
            self._validate_logical_sql_route(sql, tables)

            # ===== 主线二 Phase 3a：Gate 1 表级权限校验 =====
            # 拿到 LLM 第一版 SQL 与 tables 后立即校验，主校验，覆盖 90% 场景。
            # 启用条件：PLATFORM_DATASOURCE_ENABLED=true 且 BASE_URL 已配置。
            # fail-closed：服务异常或 SQL parse 失败 → 拒绝执行。
            gate1_result = self._do_permission_check(sql=sql, llm_tables=tables, gate_label="gate1")
            if gate1_result is not None and not gate1_result.allowed:
                raise SingleMessageError(orjson.dumps(
                    denied_tables_to_sse_payload(gate1_result, gate_label="gate1")
                ).decode())

            if ((not self.current_assistant or is_page_embedded) and is_normal_user(
                    self.current_user)) or use_dynamic_ds:
                sql_result = None

                if use_dynamic_ds:
                    dynamic_sql_result = self.generate_assistant_dynamic_sql(_session, sql, tables)
                    sqlbot_temp_sql_text = dynamic_sql_result.get(
                        'sqlbot_temp_sql_text') if dynamic_sql_result else None
                else:
                    sql_result = self.generate_filter(_session, sql, tables)  # maybe no sql and tables

                if sql_result:
                    SQLBotLogUtil.info(sql_result)
                    sql_operate = OperationEnum.GENERATE_SQL_WITH_PERMISSIONS
                    sql = self.check_save_sql(session=_session, res=sql_result, operate=sql_operate)
                elif dynamic_sql_result and sqlbot_temp_sql_text:
                    sql_operate = OperationEnum.GENERATE_DYNAMIC_SQL
                    assistant_dynamic_sql = self.check_save_sql(session=_session, res=sqlbot_temp_sql_text,
                                                                operate=sql_operate)
                else:
                    sql = self.check_save_sql(session=_session, res=full_sql_text, operate=sql_operate)
            else:
                sql = self.check_save_sql(session=_session, res=full_sql_text, operate=sql_operate)

            SQLBotLogUtil.info('sql: ' + sql)

            if not stream:
                json_result['sql'] = sql

            format_sql = sqlparse.format(sql, reindent=True)
            if in_chat:
                yield 'data:' + orjson.dumps({'content': format_sql, 'type': 'sql'}).decode() + '\n\n'
            else:
                if stream:
                    yield f'```sql\n{format_sql}\n```\n\n'

            # execute sql
            real_execute_sql = sql
            if sqlbot_temp_sql_text and assistant_dynamic_sql:
                dynamic_sql_result.pop('sqlbot_temp_sql_text')
                for origin_table, subsql in dynamic_sql_result.items():
                    assistant_dynamic_sql = assistant_dynamic_sql.replace(f'{dynamic_subsql_prefix}{origin_table}',
                                                                          subsql)
                real_execute_sql = assistant_dynamic_sql
            self._validate_logical_sql_route(real_execute_sql, None)

            if finish_step.value <= ChatFinishStep.GENERATE_SQL.value:
                yield from progress.update('permission', 'skipped', 'execution_not_requested')
                yield from progress.update('query', 'skipped', 'not_requested')
                yield from progress.update('result', 'skipped', 'not_requested')
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                if not stream:
                    yield json_result
                return

            self.current_logs[OperationEnum.EXECUTE_SQL] = start_log(session=_session,
                                                                     operate=OperationEnum.EXECUTE_SQL,
                                                                     record_id=self.record.id, local_operation=True)

            # ===== 主线二 Phase 3a：Gate 2 表级权限复核 =====
            # 防止 generate_filter / generate_assistant_filter / check_save_sql / 动态 SQL 改写
            # 引入新表绕过 Gate 1。对最终要执行的 SQL 二次解析校验。
            gate2_result = self._do_permission_check(sql=real_execute_sql, llm_tables=None, gate_label="gate2")
            if gate2_result is not None and not gate2_result.allowed:
                raise SingleMessageError(orjson.dumps(
                    denied_tables_to_sse_payload(gate2_result, gate_label="gate2")
                ).decode())

            yield from progress.update('permission', 'completed')
            yield from progress.update('query')
            result = self.execute_sql(sql=real_execute_sql)
            self.current_logs[OperationEnum.EXECUTE_SQL] = end_log(session=_session,
                                                                   log=self.current_logs[OperationEnum.EXECUTE_SQL],
                                                                   full_message={'sql': real_execute_sql,
                                                                                 'count': len(result.get('data'))})

            _data = DataFormat.convert_large_numbers_in_object_array(result.get('data'))
            result["data"] = _data

            self.save_sql_data(session=_session, data_obj=result)
            if finish_step.value > ChatFinishStep.QUERY_DATA.value:
                simple_result = self.skip_chart_model(result, chart_type, self.chat_question.question)
                chart = self.rule_table_chart(result, 'skipped' if simple_result else 'pending')
                save_chart(session=_session, chart=orjson.dumps(chart).decode(), record_id=self.record.id)
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': orjson.dumps(chart).decode(), 'type': 'chart'}).decode() + '\n\n'
            yield from progress.update('query', 'completed')
            if in_chat:
                yield 'data:' + orjson.dumps({'content': 'execute-success', 'type': 'sql-data'}).decode() + '\n\n'
            if not stream:
                json_result['data'] = get_chat_chart_data(_session, self.record.id)

            if finish_step.value <= ChatFinishStep.QUERY_DATA.value:
                yield from progress.update('result', 'skipped', 'not_requested')
                if stream:
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
                    else:
                        _column_list = []
                        for field in result.get('fields'):
                            _column_list.append(AxisObj(name=field, value=field))

                        md_data, _fields_list = DataFormat.convert_object_array_for_pandas(_column_list,
                                                                                           result.get('data'))

                        # data, _fields_list, col_formats = self.format_pd_data(_column_list, result.get('data'))

                        if not _data or not _fields_list:
                            yield 'The SQL execution result is empty.\n\n'
                        else:
                            df = pd.DataFrame(_data, columns=_fields_list)
                            df_safe = DataFormat.safe_convert_to_string(df)
                            markdown_table = df_safe.to_markdown(index=False)
                            yield markdown_table + '\n\n'
                else:
                    yield json_result
                return

            # generate chart
            yield from progress.update('result')
            result_detail = 'rule_table' if simple_result else ''
            if not simple_result:
                chart_started = time.perf_counter()
                try:
                    used_tables_schema = self.out_ds_instance.get_db_schema(
                        self.ds.id, self.chat_question.question, embedding=False,
                        table_list=tables) if self.out_ds_instance else get_table_schema(
                        session=_session,
                        current_user=self.current_user,
                        ds=self.ds,
                        question=self.chat_question.question,
                        embedding=False, table_list=tables)
                    chart_res = self.generate_chart(_session, chart_type, used_tables_schema)
                    full_chart_text = ''
                    for chunk in chart_res:
                        full_chart_text += chunk.get('content') or ''
                        if in_chat:
                            yield 'data:' + orjson.dumps(
                                {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                                 'type': 'chart-result'}).decode() + '\n\n'
                    chart = self.parse_chart(full_chart_text, result.get('fields') or [])
                    chart['render_status'] = 'ready'
                except SQLAlchemyError:
                    raise
                except Exception as exc:
                    chart = self.rule_table_chart(result, 'failed')
                    chart['render_message'] = '查询已完成，图表生成失败，已保留数据明细'
                    result_detail = 'chart_failed_table_retained'
                    if capture:
                        capture.chart_failure(exc, (time.perf_counter() - chart_started) * 1000)
                    chart_log = self.current_logs.get(OperationEnum.GENERATE_CHART)
                    if chart_log is not None:
                        trigger_log_error(_session, chart_log)
                    SQLBotLogUtil.warning(
                        f'Chart fallback record_id={self.record.id} error={type(exc).__name__}')
                # Storage failures remain fatal; only chart generation can fall back.
                save_chart(session=_session, chart=orjson.dumps(chart).decode(), record_id=self.record.id)
                if in_chat:
                    yield 'data:' + orjson.dumps(
                        {'content': orjson.dumps(chart).decode(), 'type': 'chart'}).decode() + '\n\n'
                    if chart['render_status'] == 'failed':
                        yield 'data:' + orjson.dumps(
                            {'type': 'chart-warning', 'message': chart['render_message']}).decode() + '\n\n'

            if not stream:
                json_result['chart'] = chart

            yield from progress.update('result', 'completed', result_detail)
            if not in_chat:
                if stream:
                    md_data, _fields_list = DataFormat.convert_data_fields_for_pandas(chart, result.get('fields'),
                                                                                      result.get('data'))
                    # data, _fields_list, col_formats = self.format_pd_data(_column_list, result.get('data'))

                    if not md_data or not _fields_list:
                        yield 'The SQL execution result is empty.\n\n'
                    else:
                        df = pd.DataFrame(md_data, columns=_fields_list)
                        df_safe = DataFormat.safe_convert_to_string(df)
                        markdown_table = df_safe.to_markdown(index=False)
                        yield markdown_table + '\n\n'

            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'finish'}).decode() + '\n\n'
            else:
                # generate picture
                try:
                    if chart.get('type') != 'table':
                        # yield '### generated chart picture\n\n'
                        self.current_logs[OperationEnum.GENERATE_PICTURE] = start_log(session=_session,
                                                                                      operate=OperationEnum.GENERATE_PICTURE,
                                                                                      record_id=self.record.id,
                                                                                      local_operation=True)
                        image_url, error = request_picture(self.record.chat_id, self.record.id, chart,
                                                           format_json_data(result))
                        SQLBotLogUtil.info(image_url)
                        if stream:
                            yield f'![{chart.get("type")}]({image_url})'
                        else:
                            json_result['image_url'] = image_url
                        if error is not None:
                            raise error

                        self.current_logs[OperationEnum.GENERATE_PICTURE] = end_log(session=_session,
                                                                                    log=self.current_logs[
                                                                                        OperationEnum.GENERATE_PICTURE],
                                                                                    full_message=image_url)
                except Exception as e:
                    if stream:
                        if chart.get('type') != 'table':
                            yield 'generate or fetch chart picture error.\n\n'
                        raise e

            if not stream:
                yield json_result

        except Exception as e:
            yield from progress.fail()
            if capture:
                capture.failure(e)
            traceback.print_exc()
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            elif isinstance(e, TimeoutError):
                error_msg = orjson.dumps({'type': 'query_timeout', 'message': str(e)}).decode()
            elif isinstance(e, SQLBotDBConnectionError):
                error_msg = orjson.dumps(
                    {'message': str(e), 'type': 'db-connection-err'}).decode()
            elif isinstance(e, SQLBotDBError):
                error_msg = orjson.dumps(
                    {'message': 'Execute SQL Failed', 'traceback': str(e), 'type': 'exec-sql-err'}).decode()
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            if _session:
                self.save_error(session=_session, message=error_msg)
            if in_chat:
                yield 'data:' + orjson.dumps({'content': error_msg, 'type': 'error'}).decode() + '\n\n'
            else:
                if stream:
                    yield f'&#x274c; **ERROR:**\n'
                    yield f'> {error_msg}\n'
                else:
                    json_result['success'] = False
                    json_result['message'] = error_msg
                    yield json_result
        finally:
            self.finish(_session)
            session_maker.remove()

    def run_recommend_questions_task_async(self):
        self.future = executor.submit(self.run_recommend_questions_task_cache)

    def run_recommend_questions_task_cache(self):
        from apps.operations.capture import run_captured
        run_captured(self, self.run_recommend_questions_task())

    def run_recommend_questions_task(self):
        try:
            _session = session_maker()
            res = self.generate_recommend_questions_task(_session)

            for chunk in res:
                if chunk.get('recommended_question'):
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('recommended_question'),
                         'type': 'recommended_question'}).decode() + '\n\n'
                else:
                    yield 'data:' + orjson.dumps(
                        {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                         'type': 'recommended_question_result'}).decode() + '\n\n'
        except Exception as exc:
            capture = getattr(self, 'operations_capture', None)
            if capture:
                capture.failure(exc)
            traceback.print_exc()
        finally:
            session_maker.remove()

    def run_analysis_or_predict_task_async(self, session: Session, action_type: str, base_record: ChatRecord,
                                           in_chat: bool = True, stream: bool = True):
        self.set_record(save_analysis_predict_record(session, base_record, action_type))
        self.future = executor.submit(self.run_analysis_or_predict_task_cache, action_type, in_chat, stream)

    def run_analysis_or_predict_task_cache(self, action_type: str, in_chat: bool = True, stream: bool = True):
        from apps.operations.capture import run_captured
        run_captured(self, self.run_analysis_or_predict_task(action_type, in_chat, stream))

    def run_analysis_or_predict_task(self, action_type: str, in_chat: bool = True, stream: bool = True):
        json_result: Dict[str, Any] = {'success': True}
        _session = None
        capture = getattr(self, 'operations_capture', None)
        try:
            _session = session_maker()
            if in_chat:
                yield 'data:' + orjson.dumps({'type': 'id', 'id': self.get_record().id}).decode() + '\n\n'
            else:
                if stream:
                    yield '> ' + self.trans('i18n_chat.record_id_in_mcp') + str(self.get_record().id) + '\n'
                    yield '> ' + self.get_record().question + '\n\n'
            if not stream:
                json_result['record_id'] = self.get_record().id

            if action_type == 'analysis':
                # generate analysis
                analysis_res = self.generate_analysis(_session)
                full_text = ''
                for chunk in analysis_res:
                    full_text += chunk.get('content')
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'analysis-result'}).decode() + '\n\n'
                    else:
                        if stream:
                            yield chunk.get('content')
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'info', 'msg': 'analysis generated'}).decode() + '\n\n'
                    yield 'data:' + orjson.dumps({'type': 'analysis_finish'}).decode() + '\n\n'
                else:
                    if stream:
                        yield '\n\n'
                if not stream:
                    json_result['content'] = full_text

            elif action_type == 'predict':
                # generate predict
                analysis_res = self.generate_predict(_session)
                full_text = ''
                for chunk in analysis_res:
                    full_text += chunk.get('content')
                    if in_chat:
                        yield 'data:' + orjson.dumps(
                            {'content': chunk.get('content'), 'reasoning_content': chunk.get('reasoning_content'),
                             'type': 'predict-result'}).decode() + '\n\n'
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'info', 'msg': 'predict generated'}).decode() + '\n\n'

                has_data = self.check_save_predict_data(session=_session, res=full_text)
                if has_data:
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'predict-success'}).decode() + '\n\n'
                    else:
                        chart = get_chat_chart_config(_session, self.record.id)
                        origin_data = get_chat_chart_data(_session, self.record.id)
                        predict_data = get_chat_predict_data(_session, self.record.id)

                        if stream:
                            md_data, _fields_list = DataFormat.convert_data_fields_for_pandas(chart,
                                                                                              origin_data.get('fields'),
                                                                                              predict_data)
                            if not md_data or not _fields_list:
                                yield 'Predict data result is empty.\n\n'
                            else:
                                df = pd.DataFrame(md_data, columns=_fields_list)
                                df_safe = DataFormat.safe_convert_to_string(df)
                                markdown_table = df_safe.to_markdown(index=False)
                                yield markdown_table + '\n\n'

                        else:
                            json_result['origin_data'] = origin_data
                            json_result['predict_data'] = predict_data

                        # generate picture
                        try:
                            if chart.get('type') != 'table':
                                # yield '### generated chart picture\n\n'

                                _data = get_chat_chart_data(_session, self.record.id)
                                _data['data'] = _data.get('data') + predict_data

                                image_url, error = request_picture(self.record.chat_id, self.record.id, chart,
                                                                   format_json_data(_data))
                                SQLBotLogUtil.info(image_url)
                                if stream:
                                    yield f'![{chart.get("type")}]({image_url})'
                                else:
                                    json_result['image_url'] = image_url
                                if error is not None:
                                    raise error
                        except Exception as e:
                            if stream:
                                if chart.get('type') != 'table':
                                    yield 'generate or fetch chart picture error.\n\n'
                                raise e
                else:
                    if capture:
                        capture.predict_failed = True
                    if in_chat:
                        yield 'data:' + orjson.dumps({'type': 'predict-failed'}).decode() + '\n\n'
                    else:
                        if stream:
                            yield full_text + '\n\n'
                    if not stream:
                        json_result['success'] = False
                        json_result['message'] = full_text
                if in_chat:
                    yield 'data:' + orjson.dumps({'type': 'predict_finish'}).decode() + '\n\n'

            self.finish(_session)

            if not stream:
                yield json_result
        except Exception as e:
            if capture:
                capture.failure(e)
            traceback.print_exc()
            error_msg: str
            if isinstance(e, SingleMessageError):
                error_msg = str(e)
            else:
                error_msg = orjson.dumps({'message': str(e), 'traceback': traceback.format_exc(limit=1)}).decode()
            if _session:
                self.save_error(session=_session, message=error_msg)
            if in_chat:
                yield 'data:' + orjson.dumps({'content': error_msg, 'type': 'error'}).decode() + '\n\n'
            else:
                if stream:
                    yield f'&#x274c; **ERROR:**\n'
                    yield f'> {error_msg}\n'
                else:
                    json_result['success'] = False
                    json_result['message'] = error_msg
                    yield json_result
        finally:
            # end
            session_maker.remove()

    def validate_history_ds(self, session: Session):
        _ds = self.ds
        if not self.current_assistant or self.current_assistant.type == 4:
            try:
                current_ds = session.get(CoreDatasource, _ds.id)
                if not current_ds:
                    raise SingleMessageError('chat.ds_is_invalid')
            except Exception as e:
                raise SingleMessageError("chat.ds_is_invalid")
        else:
            try:
                _ds_list: list[dict] = get_assistant_ds(session=session, llm_service=self)
                match_ds = any(item.get("id") == _ds.id for item in _ds_list)
                if not match_ds:
                    type = self.current_assistant.type
                    msg = f"[please check ds list and public ds list]" if type == 0 else f"[please check ds api]"
                    raise SingleMessageError(msg)
            except Exception as e:
                raise SingleMessageError(f"ds is invalid [{str(e)}]")

    # ===== 主线二 Phase 3a：双 Gate 表级权限校验 =====
    def _extract_authorization_header(self) -> Optional[str]:
        """
        从 current_assistant.certificate（中台 Phase 2a 注入或真实 assistant）取 Authorization 头，
        透传给中台权限服务。失败返回 None。
        """
        if not self.current_assistant or not self.current_assistant.certificate:
            return None
        try:
            cert_list = json.loads(self.current_assistant.certificate)
        except Exception:
            return None
        if not isinstance(cert_list, list):
            return None
        for item in cert_list:
            if not isinstance(item, dict):
                continue
            if item.get("target") == "header" and str(item.get("key", "")).lower() == "authorization":
                value = item.get("value")
                if value:
                    return str(value)
        return None

    def _do_permission_check(
        self, sql: str, llm_tables: Optional[list], gate_label: str,
    ) -> Optional[PermissionCheckResult]:
        """
        统一的权限校验入口。fail-closed：
          - 关闭开关时返回 None（调用方"放行"）
          - 启用时一定返回 PermissionCheckResult（allowed=True/False）
          - SQL parse 失败时返回 allowed=False，reason='sql_parse_failed'
        """
        self._check_deadline()
        if not is_permission_check_enabled():
            if settings.PLATFORM_DATASOURCE_ENABLED:
                return PermissionCheckResult(allowed=False, reason='permission_service_not_configured')
            return None

        dialect = None
        if self.ds is not None:
            dialect = getattr(self.ds, "type", None) or None

        parser_refs = extract_table_refs(sql, dialect=dialect)
        if parser_refs is None:
            # parse 失败：fail-closed 拒绝，避免越权
            SQLBotLogUtil.warning(f"[{gate_label}] SQL parser failed, deny by default")
            return PermissionCheckResult(allowed=False, reason="sql_parse_failed")

        inputs = list(parser_refs)
        if not inputs and llm_tables:
            inputs.extend(llm_tables)
        if not inputs:
            # SQL 不涉及任何表（如纯 SELECT 1），放行
            return PermissionCheckResult(allowed=True, raw_payload={"empty_tables": True})

        resolved, unresolved = self._resolve_table_inputs(inputs)
        if unresolved:
            denied = [
                DeniedTable(
                    name=str(item.get("name") or ""),
                    schema_name=item.get("schemaName"),
                    table_id=item.get("tableId"),
                    category_id=item.get("categoryId"),
                    reason=item.get("reason") or "table_not_registered_or_ambiguous",
                )
                for item in unresolved
            ]
            return PermissionCheckResult(
                allowed=False,
                reason="table_not_registered_or_ambiguous",
                denied_tables=denied,
                datasource_id=str(getattr(self.ds, "id", "")) if self.ds is not None else None,
            )

        tenant_id = getattr(self.current_user, "oid", None) if self.current_user else None
        default_ds_id = getattr(self.ds, "id", None) if self.ds is not None else None
        grouped_refs: dict[str, list[dict[str, Any]]] = {}
        for entry in resolved:
            ds_id = entry.get("datasourceId") or default_ds_id
            grouped_refs.setdefault(str(ds_id), []).append(self._permission_ref_from_entry(entry))

        denied_tables: list[DeniedTable] = []
        group_results: list[dict[str, Any]] = []
        first_denied_reason: Optional[str] = None
        first_denied_datasource_id: Optional[str] = None
        for ds_id, table_refs in grouped_refs.items():
            result = check_table_permissions(
                authorization_header=self._extract_authorization_header(),
                tenant_id=tenant_id,
                datasource_id=ds_id,
                sql=sql,
                tables=table_refs,
                chat_id=getattr(self.record, "chat_id", None) if self.record else None,
                record_id=getattr(self.record, "id", None) if self.record else None,
            )
            group_results.append({
                "datasourceId": ds_id,
                "allowed": result.allowed,
                "reason": result.reason,
                "raw": result.raw_payload,
            })
            SQLBotLogUtil.info(f"Platform record_id={getattr(self.record, 'id', None)} gate={gate_label} datasource_id={ds_id} allowed={result.allowed} tables={table_refs}")
            if not result.allowed:
                denied_tables.extend(result.denied_tables)
                if first_denied_reason is None:
                    first_denied_reason = result.reason or "user_lacks_table_permission"
                    first_denied_datasource_id = result.datasource_id or str(ds_id)

        if first_denied_reason is not None:
            return PermissionCheckResult(
                allowed=False,
                denied_tables=denied_tables,
                reason=first_denied_reason,
                datasource_id=first_denied_datasource_id,
                raw_payload={"groups": group_results},
            )

        return PermissionCheckResult(allowed=True, raw_payload={"groups": group_results})


def execute_sql_with_db(db: SQLDatabase, sql: str) -> str:
    """Execute SQL query using SQLDatabase

    Args:
        db: SQLDatabase instance
        sql: SQL query statement

    Returns:
        str: Query results formatted as string
    """
    try:
        # Execute query
        result = db.run(sql)

        if not result:
            return "Query executed successfully but returned no results."

        # Format results
        return str(result)

    except Exception as e:
        error_msg = f"SQL execution failed: {str(e)}"
        SQLBotLogUtil.exception(error_msg)
        raise RuntimeError(error_msg)


def request_picture(chat_id: int, record_id: int, chart: dict, data: dict):
    file_name = f'c_{chat_id}_r_{record_id}'

    columns = chart.get('columns') if chart.get('columns') else []
    x = None
    y = None
    series = None
    multi_quota_fields = []
    multi_quota_name = None

    if chart.get('axis'):
        axis_data = chart.get('axis')
        x = axis_data.get('x')
        y = axis_data.get('y')
        series = axis_data.get('series')
        # 获取multi-quota字段列表
        if axis_data.get('multi-quota') and 'value' in axis_data.get('multi-quota'):
            multi_quota_fields = axis_data.get('multi-quota').get('value', [])
            multi_quota_name = axis_data.get('multi-quota').get('name')

    axis = []
    for v in columns:
        axis.append({'name': v.get('name'), 'value': v.get('value')})
    if x:
        axis.append({'name': x.get('name'), 'value': x.get('value'), 'type': 'x'})
    if y:
        y_list = y if isinstance(y, list) else [y]

        for y_item in y_list:
            if isinstance(y_item, dict) and 'value' in y_item:
                y_obj = {
                    'name': y_item.get('name'),
                    'value': y_item.get('value'),
                    'type': 'y'
                }
                # 如果是multi-quota字段，添加标志
                if y_item.get('value') in multi_quota_fields:
                    y_obj['multi-quota'] = True
                axis.append(y_obj)
    if series:
        axis.append({'name': series.get('name'), 'value': series.get('value'), 'type': 'series'})
    if multi_quota_name:
        axis.append({'name': multi_quota_name, 'value': multi_quota_name, 'type': 'other-info'})

    request_obj = {
        "path": os.path.join(settings.MCP_IMAGE_PATH, file_name),
        "type": chart.get('type'),
        "data": orjson.dumps(data.get('data') if data.get('data') else []).decode(),
        "axis": orjson.dumps(axis).decode(),
    }

    _error = None
    try:
        requests.post(url=settings.MCP_IMAGE_HOST, json=request_obj, timeout=settings.SERVER_IMAGE_TIMEOUT)
    except Exception as e:
        _error = e

    request_path = urllib.parse.urljoin(settings.SERVER_IMAGE_HOST, f"{file_name}.png")

    return request_path, _error


def get_token_usage(chunk: BaseMessageChunk, token_usage: dict = None):
    try:
        if chunk.usage_metadata:
            if token_usage is None:
                token_usage = {}
            token_usage['input_tokens'] = chunk.usage_metadata.get('input_tokens')
            token_usage['output_tokens'] = chunk.usage_metadata.get('output_tokens')
            token_usage['total_tokens'] = chunk.usage_metadata.get('total_tokens')
    except Exception:
        pass


def process_stream(res: Iterator[BaseMessageChunk],
                   token_usage: Dict[str, Any] = None,
                   enable_tag_parsing: bool = settings.PARSE_REASONING_BLOCK_ENABLED,
                   start_tag: str = settings.DEFAULT_REASONING_CONTENT_START,
                   end_tag: str = settings.DEFAULT_REASONING_CONTENT_END
                   ):
    if token_usage is None:
        token_usage = {}
    in_thinking_block = False  # 标记是否在思考过程块中
    current_thinking = ''  # 当前收集的思考过程内容
    pending_start_tag = ''  # 用于缓存可能被截断的开始标签部分

    for chunk in res:
        reasoning_content_chunk = ''
        content = chunk.content
        output_content = ''  # 实际要输出的内容

        # 检查additional_kwargs中的reasoning_content
        if 'reasoning_content' in chunk.additional_kwargs:
            reasoning_content = chunk.additional_kwargs.get('reasoning_content', '')
            if reasoning_content is None:
                reasoning_content = ''

            # 累积additional_kwargs中的思考内容到current_thinking
            current_thinking += reasoning_content
            reasoning_content_chunk = reasoning_content

        # 只有当current_thinking不是空字符串时才跳过标签解析
        if not in_thinking_block and current_thinking.strip() != '':
            output_content = content  # 正常输出content
            yield {
                'content': output_content,
                'reasoning_content': reasoning_content_chunk
            }
            get_token_usage(chunk, token_usage)
            continue  # 跳过后续的标签解析逻辑

        # 如果没有有效的思考内容，并且启用了标签解析，才执行标签解析逻辑
        # 如果有缓存的开始标签部分，先拼接当前内容
        if pending_start_tag:
            content = pending_start_tag + content
            pending_start_tag = ''

        # 检查是否开始思考过程块（处理可能被截断的开始标签）
        if enable_tag_parsing and not in_thinking_block and start_tag:
            if start_tag in content:
                start_idx = content.index(start_tag)
                # 只有当开始标签前面没有其他文本时才认为是真正的思考块开始
                if start_idx == 0 or content[:start_idx].strip() == '':
                    # 完整标签存在且前面没有其他文本
                    output_content += content[:start_idx]  # 输出开始标签之前的内容
                    content = content[start_idx + len(start_tag):]  # 移除开始标签
                    in_thinking_block = True
                else:
                    # 开始标签前面有其他文本，不认为是思考块开始
                    output_content += content
                    content = ''
            else:
                # 检查是否可能有部分开始标签
                for i in range(1, len(start_tag)):
                    if content.endswith(start_tag[:i]):
                        # 只有当当前内容全是空白时才缓存部分标签
                        if content[:-i].strip() == '':
                            pending_start_tag = start_tag[:i]
                            content = content[:-i]  # 移除可能的部分标签
                            output_content += content
                            content = ''
                        break

        # 处理思考块内容
        if enable_tag_parsing and in_thinking_block and end_tag:
            if end_tag in content:
                # 找到结束标签
                end_idx = content.index(end_tag)
                current_thinking += content[:end_idx]  # 收集思考内容
                reasoning_content_chunk += current_thinking  # 添加到当前块的思考内容
                content = content[end_idx + len(end_tag):]  # 移除结束标签后的内容
                current_thinking = ''  # 重置当前思考内容
                in_thinking_block = False
                output_content += content  # 输出结束标签之后的内容
            else:
                # 在遇到结束标签前，持续收集思考内容
                current_thinking += content
                reasoning_content_chunk += content
                content = ''

        else:
            # 不在思考块中或标签解析未启用，正常输出
            output_content += content

        yield {
            'content': output_content,
            'reasoning_content': reasoning_content_chunk
        }
        get_token_usage(chunk, token_usage)


def get_lang_name(lang: str):
    if not lang:
        return '简体中文'
    normalized = lang.lower()
    if normalized.startswith('en'):
        return '英文'
    if normalized.startswith('ko'):
        return '韩语'
    return '简体中文'


def get_last_conversation_rounds(messages, rounds=settings.GENERATE_SQL_QUERY_HISTORY_ROUND_COUNT):
    """获取最后N轮对话，处理不完整对话的情况"""
    if not messages or rounds <= 0:
        return []

    # 找到所有用户消息的位置
    human_indices = []
    for index, msg in enumerate(messages):
        if msg.get('type') == 'human':
            human_indices.append(index)

    # 如果没有用户消息，返回空
    if not human_indices:
        return []

    # 计算从哪个索引开始
    if len(human_indices) <= rounds:
        # 如果用户消息数少于等于需要的轮数，从第一个用户消息开始
        start_index = human_indices[0]
    else:
        # 否则，从倒数第N个用户消息开始
        start_index = human_indices[-rounds]

    return messages[start_index:]
