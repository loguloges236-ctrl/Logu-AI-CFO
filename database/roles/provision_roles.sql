-- Least-privilege runtime role for Logu AI CFO.
--
-- Run as the database owner (the migration role, normally `logu`) or a superuser. Creating logu_app requires CREATEROLE
-- (or superuser): the owner role needs it for the first run, or run this as the bootstrap superuser (in docker-compose,
-- POSTGRES_USER is a superuser). Safe to re-run: an existing logu_app role keeps its password and grants.
-- The password is prompted for and never passed on the command line.
--
--   psql -h <host> -U logu -d logu_cfo -f database/roles/provision_roles.sql
--
-- What this creates and changes:
--   * role logu_app: LOGIN, not superuser, no CREATEDB, no CREATEROLE, no REPLICATION, no BYPASSRLS. It does NOT own
--     the database, the schema or any table.
--   * CONNECT on this database is revoked from PUBLIC and granted to logu_app only.
--   * CREATE on schema public is revoked from PUBLIC. logu_app gets USAGE only.
--   * SELECT/INSERT/UPDATE/DELETE on existing tables, USAGE/SELECT on sequences, and the same for future objects created
--     by the owner (ALTER DEFAULT PRIVILEGES). The same grants are in migration 20261009_0009.
--   * alembic_version: all privileges revoked from logu_app (only the owner touches migration state).
-- Nothing here drops, truncates, or changes existing data, and it never changes ownership.

\set ON_ERROR_STOP on
\prompt 'Password for logu_app (runtime role, at least 16 characters): ' app_password

-- psql does not substitute variables inside $$ quoting, so the length check uses psql's own control flow.
SELECT (length(:'app_password') < 16) AS password_too_short \gset
\if :password_too_short
    \echo 'ERROR: logu_app password must be at least 16 characters'
    \quit 1
\endif

SELECT format(
    'CREATE ROLE logu_app LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logu_app')
\gexec

SELECT format('REVOKE CONNECT ON DATABASE %I FROM PUBLIC', current_database()) \gexec
SELECT format('GRANT CONNECT ON DATABASE %I TO logu_app', current_database()) \gexec
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO logu_app;

-- Data grants: the same statements as migration 20261009_0009. They are repeated here so provisioning is complete
-- even when that migration ran before this role existed (its grants were then skipped and it will not run again).
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO logu_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO logu_app;
SELECT format('REVOKE ALL ON TABLE public.alembic_version FROM logu_app')
WHERE to_regclass('public.alembic_version') IS NOT NULL
\gexec
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO logu_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO logu_app;

-- Verify: the runtime role must not be a superuser and must not own the database or the schema.
DO $$
DECLARE
    app_is_super boolean;
BEGIN
    SELECT rolsuper INTO app_is_super FROM pg_roles WHERE rolname = 'logu_app';
    IF app_is_super THEN
        RAISE EXCEPTION 'logu_app must not be a superuser';
    END IF;
    IF (SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public') = 'logu_app' THEN
        RAISE EXCEPTION 'logu_app must not own schema public';
    END IF;
END
$$;
