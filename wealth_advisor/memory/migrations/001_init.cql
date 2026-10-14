-- Wealth Advisor long-term memory schema.
-- Query-first, denormalized: one table per read pattern, written together.
-- Last-write-wins only; no LWT, counters or materialized views.
-- {keyspace}, {local_dc} and {rf} are substituted by the migrator.
-- Timestamps are bigint unix milliseconds.

CREATE KEYSPACE IF NOT EXISTS {keyspace}
  WITH replication = {'class': 'NetworkTopologyStrategy', '{local_dc}': {rf}}
  AND durable_writes = true;

-- "latest N runs for a client"
CREATE TABLE IF NOT EXISTS {keyspace}.advisor_runs_by_client (
  client_id     text,
  created_at    bigint,
  run_id        text,
  status        text,
  risk_score    int,
  finding_codes set<text>,
  summary       text,
  report_json   text,
  PRIMARY KEY ((client_id), created_at, run_id)
) WITH CLUSTERING ORDER BY (created_at DESC, run_id ASC);

-- "fetch run by id"
CREATE TABLE IF NOT EXISTS {keyspace}.advisor_runs_by_id (
  run_id     text PRIMARY KEY,
  client_id  text,
  created_at bigint
);

-- "has this client had this finding before, and how often"
CREATE TABLE IF NOT EXISTS {keyspace}.findings_by_client_code (
  client_id  text,
  code       text,
  created_at bigint,
  run_id     text,
  severity   text,
  title      text,
  PRIMARY KEY ((client_id, code), created_at, run_id)
) WITH CLUSTERING ORDER BY (created_at DESC, run_id ASC);

-- Short-term session trail. Expires on its own; long-term tables have no TTL.
CREATE TABLE IF NOT EXISTS {keyspace}.session_events (
  session_id text,
  seq        int,
  client_id  text,
  created_at bigint,
  kind       text,
  payload    text,
  PRIMARY KEY ((session_id), seq)
) WITH default_time_to_live = 86400;
