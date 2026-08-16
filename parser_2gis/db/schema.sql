-- ================================
-- parser_2gis/db/schema.sql
-- Схема БД-режима парсера (схема p2gis). Применяется идемпотентно
-- при старте сервера/CLI, когда включён БД-режим (P2GIS_DB_URL).
-- Гипертаблица parse_requests создаётся в apply_schema() (Python),
-- т.к. create_hypertable требует graceful-фолбэк на обычную таблицу.
-- ================================

CREATE SCHEMA IF NOT EXISTS p2gis;

-- Мастер-записи: плоский extract_record + исходный byid-документ.
-- firm_id — дедуп между задачами и городами (upsert).
CREATE TABLE IF NOT EXISTS p2gis.records (
    firm_id           text PRIMARY KEY,
    org_id            text,
    org_name          text,
    name              text,
    description       text,
    address           text,
    address_comment   text,
    city              text,
    city_code         text,
    district          text,
    district_area     text,
    region            text,
    country           text,
    postcode          text,
    lat               double precision,
    lon               double precision,
    phone             text,
    mobile            text,
    email             text,
    websites          text[] NOT NULL DEFAULT '{}',
    socials           jsonb NOT NULL DEFAULT '{}',
    rubrics           text[] NOT NULL DEFAULT '{}',
    rubric_ids        text[] NOT NULL DEFAULT '{}',
    primary_rubric    text,
    rubric_section    text,
    sub_rubrics       text,
    rating            double precision,
    review_count      integer,
    org_rating        double precision,
    org_review_count  integer,
    average_check     text,
    schedule          jsonb,
    schedule_comment  text,
    photos            text[] NOT NULL DEFAULT '{}',
    url               text,
    reviews_url       text,
    branch_count      integer,
    nearest_station   text,
    station_distance  integer,
    search_text       text NOT NULL DEFAULT '',
    raw_doc           jsonb NOT NULL,
    last_job_id       text,
    parsed_at         timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    is_active         boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS idx_p2gis_records_city_code ON p2gis.records (city_code);
CREATE INDEX IF NOT EXISTS idx_p2gis_records_org ON p2gis.records (org_id);
CREATE INDEX IF NOT EXISTS idx_p2gis_records_updated ON p2gis.records (updated_at);
CREATE INDEX IF NOT EXISTS idx_p2gis_records_job ON p2gis.records (last_job_id);
CREATE INDEX IF NOT EXISTS idx_p2gis_records_search_trgm ON p2gis.records
    USING gin (search_text gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_p2gis_records_rubric_ids ON p2gis.records
    USING gin (rubric_ids);

-- Кэш запросов: fingerprint -> свежесть (для отдачи из БД без Chrome).
CREATE TABLE IF NOT EXISTS p2gis.request_cache (
    fingerprint    text PRIMARY KEY,
    city_code      text NOT NULL DEFAULT '',
    rubric_id      text,
    query_text     text NOT NULL DEFAULT '',
    url            text NOT NULL,
    last_parsed_at timestamptz,
    last_job_id    text,
    records_found  integer,
    status         text NOT NULL DEFAULT 'unknown',
    error          text,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_p2gis_request_cache_city ON p2gis.request_cache (city_code);

-- Журнал парсинга (гипертаблица TimescaleDB, partition по started_at).
CREATE TABLE IF NOT EXISTS p2gis.parse_requests (
    started_at       timestamptz NOT NULL,
    finished_at      timestamptz,
    job_id           text,
    fingerprint      text,
    url              text NOT NULL,
    city_code        text,
    rubric_id        text,
    query_text       text,
    cache_hit        boolean NOT NULL DEFAULT false,
    status           text NOT NULL,
    records_found    integer,
    records_updated  integer,
    records_created  integer,
    worker           text,
    error            text
);

CREATE INDEX IF NOT EXISTS idx_p2gis_parse_requests_job ON p2gis.parse_requests (job_id);
CREATE INDEX IF NOT EXISTS idx_p2gis_parse_requests_fingerprint ON p2gis.parse_requests (fingerprint);

-- Планировщик автообновления.
CREATE TABLE IF NOT EXISTS p2gis.refresh_schedules (
    id                bigserial PRIMARY KEY,
    name              text NOT NULL,
    cron              text,
    interval_minutes  integer,
    cities            text[] NOT NULL DEFAULT '{}',
    rubrics           text[] NOT NULL DEFAULT '{}',
    queries           text[] NOT NULL DEFAULT '{}',
    max_concurrent    integer,
    ttl_hours         integer,
    sync_after        boolean NOT NULL DEFAULT true,
    enabled           boolean NOT NULL DEFAULT true,
    last_run          timestamptz,
    next_run          timestamptz,
    last_status       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Курсор синхронизации p2gis -> medexpertai.
CREATE TABLE IF NOT EXISTS p2gis.sync_state (
    id               integer PRIMARY KEY DEFAULT 1,
    last_synced_at   timestamptz,
    last_error       text,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Справочники (канонические в БД-режиме, файлы — зеркало и фолбэк).
CREATE TABLE IF NOT EXISTS p2gis.cities (
    code         text PRIMARY KEY,
    name         text NOT NULL,
    domain       text NOT NULL DEFAULT 'ru',
    country_code text NOT NULL DEFAULT 'ru',
    region       text,
    source       text NOT NULL DEFAULT '2gis',   -- '2gis' | 'custom'
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_p2gis_cities_domain ON p2gis.cities (domain);
CREATE INDEX IF NOT EXISTS idx_p2gis_cities_region ON p2gis.cities (region);

-- Миграция для существующих БД (идемпотентно).
ALTER TABLE p2gis.cities ADD COLUMN IF NOT EXISTS region text;

CREATE TABLE IF NOT EXISTS p2gis.rubrics (
    code        text PRIMARY KEY,
    label       text NOT NULL DEFAULT '',
    parent_code text,
    node        jsonb NOT NULL,                  -- полный узел рубрикатора
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_p2gis_rubrics_parent ON p2gis.rubrics (parent_code);
