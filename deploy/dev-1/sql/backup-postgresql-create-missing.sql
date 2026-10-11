-- Only missing tables and their required keys/sequences; existing tables remain unchanged.

-- Requires vector extension. Never writes alembic_version or substitutes for full Alembic initialization.

DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector') THEN RAISE EXCEPTION 'Enable vector before applying backup'; END IF; END $$;

DO $repair$ BEGIN
IF to_regclass('public.ai_model') IS NULL THEN
CREATE TABLE public.ai_model (
    id bigint NOT NULL,
    api_key text,
    api_domain text NOT NULL,
    protocol integer NOT NULL,
    supplier integer NOT NULL,
    name character varying(255) NOT NULL,
    model_type integer NOT NULL,
    base_model character varying(255) NOT NULL,
    default_model boolean NOT NULL,
    config text NOT NULL,
    status integer NOT NULL,
    create_time bigint NOT NULL
);
CREATE SEQUENCE public.ai_model_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.ai_model_id_seq OWNED BY public.ai_model.id;
ALTER TABLE ONLY public.ai_model ALTER COLUMN id SET DEFAULT nextval('public.ai_model_id_seq'::regclass);
ALTER TABLE ONLY public.ai_model
    ADD CONSTRAINT ai_model_pkey PRIMARY KEY (id);
CREATE INDEX ix_ai_model_id ON public.ai_model USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.chat') IS NULL THEN
CREATE TABLE public.chat (
    id bigint NOT NULL,
    create_time timestamp without time zone,
    create_by bigint,
    brief character varying(64),
    chat_type character varying(20) NOT NULL,
    datasource bigint,
    engine_type character varying(64) NOT NULL,
    oid bigint,
    origin integer,
    brief_generate boolean DEFAULT false NOT NULL,
    recommended_question_answer text,
    recommended_question text,
    recommended_generate boolean
);
ALTER TABLE public.chat ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.chat_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.chat
    ADD CONSTRAINT chat_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.chat_log') IS NULL THEN
CREATE TABLE public.chat_log (
    id bigint NOT NULL,
    type character varying(3),
    operate character varying(3),
    pid bigint,
    ai_modal_id bigint,
    base_modal character varying(255),
    messages jsonb,
    reasoning_content text,
    start_time timestamp without time zone,
    finish_time timestamp without time zone,
    token_usage jsonb,
    local_operation boolean NOT NULL,
    error boolean NOT NULL
);
ALTER TABLE public.chat_log ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.chat_log_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.chat_log
    ADD CONSTRAINT chat_log_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.chat_record') IS NULL THEN
CREATE TABLE public.chat_record (
    id bigint NOT NULL,
    chat_id bigint NOT NULL,
    create_time timestamp without time zone,
    finish_time timestamp without time zone,
    create_by bigint,
    datasource bigint,
    engine_type character varying(64),
    question text,
    sql_answer text,
    sql text,
    sql_exec_result text,
    data text,
    chart_answer text,
    chart text,
    finish boolean,
    error text,
    analysis text,
    predict text,
    predict_data text,
    ai_modal_id bigint,
    first_chat boolean,
    recommended_question_answer text,
    recommended_question text,
    datasource_select_answer text,
    analysis_record_id bigint,
    predict_record_id bigint,
    regenerate_record_id bigint
);
ALTER TABLE public.chat_record ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.chat_record_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.chat_record
    ADD CONSTRAINT chat_record_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.core_dashboard') IS NULL THEN
CREATE TABLE public.core_dashboard (
    id character varying(50) NOT NULL,
    name character varying(255),
    pid character varying(50),
    workspace_id character varying(50),
    org_id character varying(50),
    level integer,
    node_type character varying(255),
    type character varying(50),
    canvas_style_data text,
    component_data text,
    mobile_layout smallint,
    status integer,
    self_watermark_status integer,
    sort integer,
    create_time bigint,
    create_by character varying(255),
    update_time bigint,
    update_by character varying(255),
    remark character varying(255),
    source character varying(255),
    delete_flag smallint,
    delete_time bigint,
    delete_by character varying(255),
    version integer,
    content_id character varying(50),
    check_version character varying(50),
    canvas_view_info text
);
ALTER TABLE ONLY public.core_dashboard
    ADD CONSTRAINT core_dashboard_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.core_datasource') IS NULL THEN
CREATE TABLE public.core_datasource (
    id bigint NOT NULL,
    name character varying(128) NOT NULL,
    description character varying(512),
    type character varying(64) NOT NULL,
    configuration text,
    create_time timestamp without time zone,
    create_by bigint,
    status character varying(64),
    type_name character varying(64),
    num character varying(256),
    oid bigint,
    table_relation jsonb,
    embedding text,
    recommended_config bigint
);
ALTER TABLE public.core_datasource ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.core_datasource_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.core_datasource
    ADD CONSTRAINT core_datasource_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.core_field') IS NULL THEN
CREATE TABLE public.core_field (
    id bigint NOT NULL,
    ds_id bigint,
    table_id bigint,
    checked boolean NOT NULL,
    field_name text,
    field_type character varying(128),
    field_comment text,
    custom_comment text,
    field_index bigint
);
ALTER TABLE public.core_field ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.core_field_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.core_field
    ADD CONSTRAINT core_field_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.core_table') IS NULL THEN
CREATE TABLE public.core_table (
    id bigint NOT NULL,
    ds_id bigint,
    checked boolean NOT NULL,
    table_name text,
    table_comment text,
    custom_comment text,
    embedding text
);
ALTER TABLE public.core_table ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.core_table_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.core_table
    ADD CONSTRAINT core_table_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.custom_prompt') IS NULL THEN
CREATE TABLE public.custom_prompt (
    id bigint NOT NULL,
    oid bigint,
    type character varying(20),
    create_time timestamp without time zone,
    name character varying(255),
    prompt text,
    specific_ds boolean,
    datasource_ids jsonb
);
ALTER TABLE public.custom_prompt ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.custom_prompt_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.custom_prompt
    ADD CONSTRAINT custom_prompt_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.data_training') IS NULL THEN
CREATE TABLE public.data_training (
    id bigint NOT NULL,
    oid bigint,
    datasource bigint,
    create_time timestamp without time zone,
    question character varying(255),
    description text,
    embedding public.vector,
    enabled boolean,
    advanced_application bigint
);
ALTER TABLE public.data_training ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.data_training_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.data_training
    ADD CONSTRAINT data_training_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.ds_permission') IS NULL THEN
CREATE TABLE public.ds_permission (
    id bigint NOT NULL,
    enable boolean NOT NULL,
    auth_target_type character varying(128),
    auth_target_id bigint,
    type character varying(64) NOT NULL,
    ds_id bigint,
    table_id bigint,
    expression_tree text,
    permissions text,
    white_list_user text,
    create_time timestamp without time zone,
    name character varying(128)
);
ALTER TABLE public.ds_permission ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ds_permission_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.ds_permission
    ADD CONSTRAINT ds_permission_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.ds_recommended_problem') IS NULL THEN
CREATE TABLE public.ds_recommended_problem (
    id bigint NOT NULL,
    datasource_id bigint,
    question text,
    remark text,
    sort bigint,
    create_time timestamp(6) without time zone,
    create_by bigint
);
ALTER TABLE public.ds_recommended_problem ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ds_recommended_problem_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 9999999999
    CACHE 1
);
ALTER TABLE ONLY public.ds_recommended_problem
    ADD CONSTRAINT ds_recommended_problem_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.ds_rules') IS NULL THEN
CREATE TABLE public.ds_rules (
    id integer NOT NULL,
    enable boolean NOT NULL,
    name character varying(128) NOT NULL,
    description character varying(512),
    permission_list text,
    user_list text,
    white_list_user text,
    create_time timestamp without time zone,
    oid bigint
);
ALTER TABLE public.ds_rules ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ds_rules_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.ds_rules
    ADD CONSTRAINT ds_rules_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.license') IS NULL THEN
CREATE TABLE public.license (
    id bigint NOT NULL,
    license_key text NOT NULL,
    f2c_license text NOT NULL,
    create_time bigint NOT NULL,
    update_time bigint NOT NULL
);
CREATE SEQUENCE public.license_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.license_id_seq OWNED BY public.license.id;
ALTER TABLE ONLY public.license ALTER COLUMN id SET DEFAULT nextval('public.license_id_seq'::regclass);
ALTER TABLE ONLY public.license
    ADD CONSTRAINT license_pkey PRIMARY KEY (id);
CREATE INDEX ix_license_id ON public.license USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.platform_embedding_sync_job') IS NULL THEN
CREATE TABLE public.platform_embedding_sync_job (
    id character varying(36) NOT NULL,
    tenant_id bigint NOT NULL,
    datasource_id bigint NOT NULL,
    user_id bigint NOT NULL,
    status character varying(20) NOT NULL,
    snapshot jsonb NOT NULL,
    force boolean NOT NULL,
    counts jsonb NOT NULL,
    errors jsonb NOT NULL,
    created_at timestamp with time zone NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone
);
ALTER TABLE ONLY public.platform_embedding_sync_job
    ADD CONSTRAINT platform_embedding_sync_job_pkey PRIMARY KEY (id);
CREATE INDEX ix_platform_embedding_latest_job ON public.platform_embedding_sync_job USING btree (tenant_id, datasource_id, created_at);
CREATE UNIQUE INDEX uq_platform_embedding_active_job ON public.platform_embedding_sync_job USING btree (tenant_id, datasource_id) WHERE ((status)::text = ANY ((ARRAY['pending'::character varying, 'running'::character varying])::text[]));
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.platform_table_embedding') IS NULL THEN
CREATE TABLE public.platform_table_embedding (
    id bigint NOT NULL,
    tenant_id bigint NOT NULL,
    datasource_id bigint NOT NULL,
    table_id bigint NOT NULL,
    metadata_json jsonb NOT NULL,
    metadata_hash character varying(64) NOT NULL,
    model_version character varying(64) NOT NULL,
    active boolean NOT NULL,
    created_at timestamp with time zone NOT NULL,
    updated_at timestamp with time zone NOT NULL,
    synced_at timestamp with time zone NOT NULL
);
CREATE SEQUENCE public.platform_table_embedding_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.platform_table_embedding_id_seq OWNED BY public.platform_table_embedding.id;
ALTER TABLE ONLY public.platform_table_embedding ALTER COLUMN id SET DEFAULT nextval('public.platform_table_embedding_id_seq'::regclass);
ALTER TABLE ONLY public.platform_table_embedding
    ADD CONSTRAINT platform_table_embedding_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.platform_table_embedding
    ADD CONSTRAINT uq_platform_embedding_table UNIQUE (tenant_id, datasource_id, table_id);
CREATE INDEX ix_platform_embedding_scope ON public.platform_table_embedding USING btree (tenant_id, datasource_id, active);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.platform_table_embedding_chunk') IS NULL THEN
CREATE TABLE public.platform_table_embedding_chunk (
    id bigint NOT NULL,
    parent_id bigint NOT NULL,
    ordinal integer NOT NULL,
    content text NOT NULL,
    embedding public.vector(768) NOT NULL
);
CREATE SEQUENCE public.platform_table_embedding_chunk_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.platform_table_embedding_chunk_id_seq OWNED BY public.platform_table_embedding_chunk.id;
ALTER TABLE ONLY public.platform_table_embedding_chunk ALTER COLUMN id SET DEFAULT nextval('public.platform_table_embedding_chunk_id_seq'::regclass);
ALTER TABLE ONLY public.platform_table_embedding_chunk
    ADD CONSTRAINT platform_table_embedding_chunk_pkey PRIMARY KEY (id);
ALTER TABLE ONLY public.platform_table_embedding_chunk
    ADD CONSTRAINT uq_platform_embedding_chunk UNIQUE (parent_id, ordinal);
ALTER TABLE ONLY public.platform_table_embedding_chunk
    ADD CONSTRAINT platform_table_embedding_chunk_parent_id_fkey FOREIGN KEY (parent_id) REFERENCES public.platform_table_embedding(id) ON DELETE CASCADE;
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.rsa') IS NULL THEN
CREATE TABLE public.rsa (
    id bigint NOT NULL,
    private_key text NOT NULL,
    public_key text NOT NULL,
    salt text NOT NULL,
    create_time bigint NOT NULL,
    update_time bigint NOT NULL
);
CREATE SEQUENCE public.rsa_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.rsa_id_seq OWNED BY public.rsa.id;
ALTER TABLE ONLY public.rsa ALTER COLUMN id SET DEFAULT nextval('public.rsa_id_seq'::regclass);
ALTER TABLE ONLY public.rsa
    ADD CONSTRAINT rsa_pkey PRIMARY KEY (id);
CREATE INDEX ix_rsa_id ON public.rsa USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_apikey') IS NULL THEN
CREATE TABLE public.sys_apikey (
    access_key character varying(255) NOT NULL,
    secret_key character varying(255) NOT NULL,
    create_time bigint NOT NULL,
    uid bigint NOT NULL,
    status boolean NOT NULL,
    id bigint NOT NULL
);
CREATE SEQUENCE public.sys_apikey_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_apikey_id_seq OWNED BY public.sys_apikey.id;
ALTER TABLE ONLY public.sys_apikey ALTER COLUMN id SET DEFAULT nextval('public.sys_apikey_id_seq'::regclass);
ALTER TABLE ONLY public.sys_apikey
    ADD CONSTRAINT sys_apikey_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_apikey_id ON public.sys_apikey USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_arg') IS NULL THEN
CREATE TABLE public.sys_arg (
    id bigint NOT NULL,
    pkey character varying(255) NOT NULL,
    pval character varying(255),
    ptype character varying(255) DEFAULT 'str'::character varying NOT NULL,
    sort_no integer DEFAULT 1 NOT NULL
);
CREATE SEQUENCE public.sys_arg_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_arg_id_seq OWNED BY public.sys_arg.id;
ALTER TABLE ONLY public.sys_arg ALTER COLUMN id SET DEFAULT nextval('public.sys_arg_id_seq'::regclass);
ALTER TABLE ONLY public.sys_arg
    ADD CONSTRAINT sys_arg_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_arg_id ON public.sys_arg USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_assistant') IS NULL THEN
CREATE TABLE public.sys_assistant (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    domain character varying(255) NOT NULL,
    type integer NOT NULL,
    configuration text,
    create_time bigint NOT NULL,
    description text,
    app_id character varying(255),
    app_secret character varying(255),
    oid bigint
);
CREATE SEQUENCE public.sys_assistant_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_assistant_id_seq OWNED BY public.sys_assistant.id;
ALTER TABLE ONLY public.sys_assistant ALTER COLUMN id SET DEFAULT nextval('public.sys_assistant_id_seq'::regclass);
ALTER TABLE ONLY public.sys_assistant
    ADD CONSTRAINT sys_assistant_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_assistant_id ON public.sys_assistant USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_authentication') IS NULL THEN
CREATE TABLE public.sys_authentication (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    type integer NOT NULL,
    config text,
    enable boolean NOT NULL,
    valid boolean NOT NULL,
    create_time bigint NOT NULL
);
CREATE SEQUENCE public.sys_authentication_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_authentication_id_seq OWNED BY public.sys_authentication.id;
ALTER TABLE ONLY public.sys_authentication ALTER COLUMN id SET DEFAULT nextval('public.sys_authentication_id_seq'::regclass);
ALTER TABLE ONLY public.sys_authentication
    ADD CONSTRAINT sys_authentication_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_authentication_id ON public.sys_authentication USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_logs') IS NULL THEN
CREATE TABLE public.sys_logs (
    id bigint NOT NULL,
    operation_type character varying(255),
    operation_detail text,
    user_id bigint,
    operation_status character varying(255),
    oid bigint,
    ip_address character varying(255),
    user_agent character varying(255),
    execution_time bigint,
    error_message text,
    create_time timestamp without time zone,
    module character varying(255),
    remark character varying(255),
    resource_id text,
    request_method character varying(255),
    request_path character varying(255),
    user_name character varying(255),
    resource_name text
);
CREATE SEQUENCE public.sys_logs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_logs_id_seq OWNED BY public.sys_logs.id;
ALTER TABLE ONLY public.sys_logs ALTER COLUMN id SET DEFAULT nextval('public.sys_logs_id_seq'::regclass);
ALTER TABLE ONLY public.sys_logs
    ADD CONSTRAINT sys_logs_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_logs_resource') IS NULL THEN
CREATE TABLE public.sys_logs_resource (
    id bigint NOT NULL,
    resource_id character varying(255),
    module character varying(255),
    resource_name character varying(255),
    log_id bigint
);
ALTER TABLE public.sys_logs_resource ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.sys_logs_resource_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    MAXVALUE 999999999999999
    CACHE 1
);
ALTER TABLE ONLY public.sys_logs_resource
    ADD CONSTRAINT sys_logs_resource_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_platform_token') IS NULL THEN
CREATE TABLE public.sys_platform_token (
    id bigint NOT NULL,
    token character varying(255) NOT NULL,
    create_time bigint NOT NULL,
    exp_time bigint NOT NULL
);
CREATE SEQUENCE public.sys_platform_token_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_platform_token_id_seq OWNED BY public.sys_platform_token.id;
ALTER TABLE ONLY public.sys_platform_token ALTER COLUMN id SET DEFAULT nextval('public.sys_platform_token_id_seq'::regclass);
ALTER TABLE ONLY public.sys_platform_token
    ADD CONSTRAINT sys_platform_token_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_platform_token_id ON public.sys_platform_token USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_user') IS NULL THEN
CREATE TABLE public.sys_user (
    id bigint NOT NULL,
    account character varying(255) NOT NULL,
    name character varying(255) NOT NULL,
    password character varying(255) NOT NULL,
    email character varying(255),
    oid bigint NOT NULL,
    status integer NOT NULL,
    create_time bigint NOT NULL,
    language character varying(255) DEFAULT 'zh-CN'::character varying NOT NULL,
    origin integer DEFAULT 0 NOT NULL,
    system_variables jsonb
);
CREATE SEQUENCE public.sys_user_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_user_id_seq OWNED BY public.sys_user.id;
ALTER TABLE ONLY public.sys_user ALTER COLUMN id SET DEFAULT nextval('public.sys_user_id_seq'::regclass);
ALTER TABLE ONLY public.sys_user
    ADD CONSTRAINT sys_user_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_user_platform') IS NULL THEN
CREATE TABLE public.sys_user_platform (
    uid bigint NOT NULL,
    origin integer DEFAULT 0 NOT NULL,
    platform_uid character varying(255) NOT NULL,
    id bigint NOT NULL
);
CREATE SEQUENCE public.sys_user_platform_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_user_platform_id_seq OWNED BY public.sys_user_platform.id;
ALTER TABLE ONLY public.sys_user_platform ALTER COLUMN id SET DEFAULT nextval('public.sys_user_platform_id_seq'::regclass);
ALTER TABLE ONLY public.sys_user_platform
    ADD CONSTRAINT sys_user_platform_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_user_platform_id ON public.sys_user_platform USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_user_ws') IS NULL THEN
CREATE TABLE public.sys_user_ws (
    id bigint NOT NULL,
    uid bigint NOT NULL,
    oid bigint NOT NULL,
    weight integer NOT NULL
);
CREATE SEQUENCE public.sys_user_ws_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_user_ws_id_seq OWNED BY public.sys_user_ws.id;
ALTER TABLE ONLY public.sys_user_ws ALTER COLUMN id SET DEFAULT nextval('public.sys_user_ws_id_seq'::regclass);
ALTER TABLE ONLY public.sys_user_ws
    ADD CONSTRAINT sys_user_ws_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_user_ws_id ON public.sys_user_ws USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.sys_workspace') IS NULL THEN
CREATE TABLE public.sys_workspace (
    id bigint NOT NULL,
    name character varying(255) NOT NULL,
    create_time bigint NOT NULL
);
CREATE SEQUENCE public.sys_workspace_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.sys_workspace_id_seq OWNED BY public.sys_workspace.id;
ALTER TABLE ONLY public.sys_workspace ALTER COLUMN id SET DEFAULT nextval('public.sys_workspace_id_seq'::regclass);
ALTER TABLE ONLY public.sys_workspace
    ADD CONSTRAINT sys_workspace_pkey PRIMARY KEY (id);
CREATE INDEX ix_sys_workspace_id ON public.sys_workspace USING btree (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.system_variable') IS NULL THEN
CREATE TABLE public.system_variable (
    id bigint NOT NULL,
    name character varying(128) NOT NULL,
    var_type character varying(128) NOT NULL,
    type character varying(128) NOT NULL,
    value jsonb,
    create_time timestamp without time zone,
    create_by bigint
);
ALTER TABLE public.system_variable ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.system_variable_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.system_variable
    ADD CONSTRAINT system_variable_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.terminology') IS NULL THEN
CREATE TABLE public.terminology (
    id bigint NOT NULL,
    pid bigint,
    create_time timestamp without time zone,
    word character varying(255),
    description text,
    embedding public.vector,
    oid bigint,
    specific_ds boolean,
    datasource_ids jsonb,
    enabled boolean
);
ALTER TABLE public.terminology ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.terminology_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);
ALTER TABLE ONLY public.terminology
    ADD CONSTRAINT terminology_pkey PRIMARY KEY (id);
END IF;
END $repair$;

DO $repair$ BEGIN
IF to_regclass('public.terms') IS NULL THEN
CREATE TABLE public.terms (
    id bigint NOT NULL,
    term character varying(255) NOT NULL,
    definition character varying(255) NOT NULL,
    domain character varying(255) NOT NULL,
    create_time integer NOT NULL
);
CREATE SEQUENCE public.terms_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;
ALTER SEQUENCE public.terms_id_seq OWNED BY public.terms.id;
ALTER TABLE ONLY public.terms ALTER COLUMN id SET DEFAULT nextval('public.terms_id_seq'::regclass);
ALTER TABLE ONLY public.terms
    ADD CONSTRAINT terms_pkey PRIMARY KEY (id);
CREATE INDEX ix_terms_id ON public.terms USING btree (id);
END IF;
END $repair$;
