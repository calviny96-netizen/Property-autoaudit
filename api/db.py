import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from tenant import workspace_id


def database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit
    host = os.getenv("DATABASE_HOST", "host.docker.internal")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.environ["POSTGRES_USER"]
    password = os.environ["POSTGRES_PASSWORD"]
    database = os.getenv("POSTGRES_DB", "postgres")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


def connect():
    # Docker's shared PostgreSQL has a small /dev/shm. Concurrent parallel
    # aggregates can exhaust it on the full archive; scope this to XM sessions.
    # The single background worker may use more sort memory and asynchronous commits
    # (its writes are rebuildable); the API keeps the conservative defaults.
    options = (f"-c max_parallel_workers_per_gather=0 -c work_mem={os.getenv('XM_PG_WORK_MEM', '32MB')}"
               f" -c synchronous_commit={os.getenv('XM_PG_SYNCHRONOUS_COMMIT', 'on')}")
    conn = psycopg.connect(database_url(), row_factory=dict_row, options=options)
    conn.execute("SELECT set_config('xm.workspace_id',%s,false)", (workspace_id(),))
    return conn


def ensure_schema() -> None:
    sql = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
    with connect() as conn:
        # A running reprocess holds table locks for its whole transaction. The schema is
        # idempotent and was already applied by whichever process started first, so startup
        # must not wait on those locks (it kept the API down with 502s).
        conn.execute("SET lock_timeout = '10s'")
        try:
            conn.execute(sql)
            conn.commit()
        except psycopg.errors.LockNotAvailable:
            conn.rollback()
            print("Schema check skipped: tables are locked by a running job; it runs again on the next start.", flush=True)
