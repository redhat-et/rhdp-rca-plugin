"""Embed completed RCA analyses into pgvector for historical similarity search."""

from __future__ import annotations

import json
import os
import re
from typing import Any

# Lazy-loaded SentenceTransformer singleton (avoids import-time model load).
_st_model = None

MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384
DEFAULT_TABLE = "rca_analysis_embeddings"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise EnvironmentError(f"Missing required environment variable: {name}")
    return value


def _table_name(table: str | None = None) -> str:
    name = table or os.environ.get("PGVECTOR_TABLE") or DEFAULT_TABLE
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"Invalid PGVECTOR_TABLE name: {name!r}")
    return name


def get_connection():
    """Connect using PGVECTOR_* vars, falling back to SOURCE_DB_* credentials."""
    import psycopg

    host = _require_env("PGVECTOR_HOST")
    port = os.environ.get("PGVECTOR_PORT") or os.environ.get("SOURCE_DB_PORT") or "5432"
    dbname = (
        os.environ.get("PGVECTOR_DB_NAME")
        or os.environ.get("SOURCE_DB_NAME")
        or "postgres"
    )
    user = (
        os.environ.get("PGVECTOR_DB_USER")
        or os.environ.get("SOURCE_DB_USER")
        or "postgres"
    )
    password = (
        os.environ.get("PGVECTOR_DB_PASSWORD")
        or os.environ.get("SOURCE_DB_PASSWORD")
        or ""
    )
    return psycopg.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
    )


def _get_embedding_model():
    global _st_model
    if _st_model is None:
        from sentence_transformers import SentenceTransformer

        _st_model = SentenceTransformer(MODEL_NAME)
    return _st_model


def get_embedding(text: str) -> list[float]:
    """Embed a single string into a 384-dimensional vector."""
    if not text or not isinstance(text, str):
        raise ValueError("Input must be a non-empty string.")
    embedding = _get_embedding_model().encode(text)
    return embedding.tolist()


def _compact_failed_tasks(failed_tasks: list[dict] | None) -> list[dict[str, str]]:
    """Keep only the fields useful for retrieval/display."""
    result: list[dict[str, str]] = []
    for task in failed_tasks or []:
        if not isinstance(task, dict):
            continue
        entry = {
            "task": str(task.get("task") or ""),
            "error_message": str(task.get("error_message") or ""),
            "task_action": str(task.get("task_action") or ""),
        }
        if any(entry.values()):
            result.append(entry)
    return result


def _collect_github_paths(summary: dict[str, Any]) -> list[str]:
    """Collect unique github_path values from evidence and recommendations."""
    paths: list[str] = []
    seen: set[str] = set()
    for section in ("evidence", "recommendations"):
        for item in summary.get(section) or []:
            if not isinstance(item, dict):
                continue
            path = item.get("github_path")
            if path and path not in seen:
                seen.add(path)
                paths.append(str(path))
    return paths


def build_rca_metadata(
    job_context: dict[str, Any],
    github_context: dict[str, Any] | None,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Assemble one embedding row's metadata from step1/step4/step5 artifacts."""
    job_meta = (github_context or {}).get("job_metadata") or {}
    root_cause = summary.get("root_cause") or {}
    time_window = job_context.get("time_window") or {}

    return {
        "job_id": str(
            summary.get("job_id") or job_context.get("job_id") or ""
        ),
        "guid": job_context.get("guid") or "",
        "namespace": job_context.get("namespace") or "",
        "cluster": job_context.get("cluster") or "",
        "status": job_context.get("status") or "",
        "action": job_context.get("action") or "",
        "cloud_provider": job_context.get("cloud_provider") or "",
        "env_type": job_context.get("env_type") or "",
        "platform": job_meta.get("platform") or "",
        "catalog_item": job_meta.get("catalog_item") or "",
        "env": job_meta.get("env") or "",
        "job_duration_seconds": time_window.get("duration_seconds"),
        "root_cause_category": root_cause.get("category") or "",
        "root_cause_summary": root_cause.get("summary") or "",
        "confidence": root_cause.get("confidence") or "",
        "contributing_factors": list(summary.get("contributing_factors") or []),
        "failed_tasks": _compact_failed_tasks(job_context.get("failed_tasks")),
        "github_paths": _collect_github_paths(summary),
        "recommendation_count": len(summary.get("recommendations") or []),
        "analyzed_at": summary.get("analyzed_at") or "",
    }


def build_embedding_text(metadata: dict[str, Any]) -> str:
    """Build retrieval-friendly text weighted toward semantic failure content."""
    parts: list[str] = []

    category = metadata.get("root_cause_category") or ""
    summary = metadata.get("root_cause_summary") or ""
    if category or summary:
        parts.append(f"Root cause ({category}): {summary}".strip())

    catalog = metadata.get("catalog_item") or ""
    platform = metadata.get("platform") or ""
    env = metadata.get("env") or ""
    env_bits = [b for b in (platform, catalog, env) if b]
    if env_bits:
        parts.append(f"Catalog: {' / '.join(env_bits)}")

    cloud = metadata.get("cloud_provider") or ""
    env_type = metadata.get("env_type") or ""
    action = metadata.get("action") or ""
    ctx_bits = [b for b in (cloud, env_type, action) if b]
    if ctx_bits:
        parts.append(f"Context: {' '.join(ctx_bits)}")

    factors = metadata.get("contributing_factors") or []
    if factors:
        parts.append("Contributing factors: " + "; ".join(str(f) for f in factors))

    failed = metadata.get("failed_tasks") or []
    for task in failed:
        if not isinstance(task, dict):
            continue
        name = task.get("task") or "unknown task"
        err = task.get("error_message") or ""
        action_name = task.get("task_action") or ""
        line = f"Failed task: {name}"
        if action_name:
            line += f" ({action_name})"
        if err:
            line += f" — {err}"
        parts.append(line)

    return "\n".join(parts).strip()


def setup_pgvector_table(table: str | None = None) -> None:
    """Enable pgvector, create the metadata table if missing, and add an HNSW index."""
    from pgvector.psycopg import register_vector

    table_name = _table_name(table)
    index_name = f"{table_name}_embedding_hnsw_idx"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        conn.commit()

        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id SERIAL PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE,
                    guid TEXT,
                    namespace TEXT,
                    cluster TEXT,
                    status TEXT,
                    action TEXT,
                    cloud_provider TEXT,
                    env_type TEXT,
                    platform TEXT,
                    catalog_item TEXT,
                    env TEXT,
                    job_duration_seconds DOUBLE PRECISION,
                    root_cause_category TEXT,
                    root_cause_summary TEXT,
                    confidence TEXT,
                    contributing_factors JSONB,
                    failed_tasks JSONB,
                    github_paths JSONB,
                    recommendation_count INTEGER,
                    analyzed_at TEXT,
                    embedding_text TEXT NOT NULL,
                    embedding vector({EMBEDDING_DIM}) NOT NULL
                );
                """
            )
            cur.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {index_name}
                ON {table_name}
                USING hnsw (embedding vector_cosine_ops);
                """
            )
        conn.commit()
    print(f"Table {table_name!r} and pgvector extension ready.")


def upsert_rca_embedding(
    metadata: dict[str, Any],
    embedding_text: str | None = None,
    table: str | None = None,
) -> int:
    """Embed metadata and upsert into the configured table. Returns row id."""
    from pgvector.psycopg import register_vector

    job_id = metadata.get("job_id")
    if not job_id:
        raise ValueError("metadata.job_id is required")

    text = embedding_text if embedding_text is not None else build_embedding_text(metadata)
    if not text:
        raise ValueError("embedding text is empty; nothing meaningful to embed")

    embedding = get_embedding(text)
    table_name = _table_name(table)

    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {table_name} (
                    job_id, guid, namespace, cluster, status, action,
                    cloud_provider, env_type, platform, catalog_item, env,
                    job_duration_seconds, root_cause_category, root_cause_summary,
                    confidence, contributing_factors, failed_tasks, github_paths,
                    recommendation_count, analyzed_at, embedding_text, embedding
                ) VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s::jsonb, %s::jsonb, %s::jsonb,
                    %s, %s, %s, %s
                )
                ON CONFLICT (job_id) DO UPDATE SET
                    guid = EXCLUDED.guid,
                    namespace = EXCLUDED.namespace,
                    cluster = EXCLUDED.cluster,
                    status = EXCLUDED.status,
                    action = EXCLUDED.action,
                    cloud_provider = EXCLUDED.cloud_provider,
                    env_type = EXCLUDED.env_type,
                    platform = EXCLUDED.platform,
                    catalog_item = EXCLUDED.catalog_item,
                    env = EXCLUDED.env,
                    job_duration_seconds = EXCLUDED.job_duration_seconds,
                    root_cause_category = EXCLUDED.root_cause_category,
                    root_cause_summary = EXCLUDED.root_cause_summary,
                    confidence = EXCLUDED.confidence,
                    contributing_factors = EXCLUDED.contributing_factors,
                    failed_tasks = EXCLUDED.failed_tasks,
                    github_paths = EXCLUDED.github_paths,
                    recommendation_count = EXCLUDED.recommendation_count,
                    analyzed_at = EXCLUDED.analyzed_at,
                    embedding_text = EXCLUDED.embedding_text,
                    embedding = EXCLUDED.embedding
                RETURNING id
                """,
                (
                    job_id,
                    metadata.get("guid") or None,
                    metadata.get("namespace") or None,
                    metadata.get("cluster") or None,
                    metadata.get("status") or None,
                    metadata.get("action") or None,
                    metadata.get("cloud_provider") or None,
                    metadata.get("env_type") or None,
                    metadata.get("platform") or None,
                    metadata.get("catalog_item") or None,
                    metadata.get("env") or None,
                    metadata.get("job_duration_seconds"),
                    metadata.get("root_cause_category") or None,
                    metadata.get("root_cause_summary") or None,
                    metadata.get("confidence") or None,
                    json.dumps(metadata.get("contributing_factors") or []),
                    json.dumps(metadata.get("failed_tasks") or []),
                    json.dumps(metadata.get("github_paths") or []),
                    metadata.get("recommendation_count") or 0,
                    metadata.get("analyzed_at") or None,
                    text,
                    embedding,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        raise RuntimeError(f"Upsert failed for job_id={job_id}")
    return int(row[0])


def query_similar(
    text: str,
    limit: int = 5,
    category: str | None = None,
    catalog_item: str | None = None,
    table: str | None = None,
) -> list[dict[str, Any]]:
    """
    Embed a query and return nearest rows by cosine distance.

    Optional filters narrow results by root_cause_category and/or catalog_item.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")
    if not text or not isinstance(text, str):
        raise ValueError("Query text must be a non-empty string.")

    from pgvector.psycopg import register_vector

    table_name = _table_name(table)
    embedding = get_embedding(text)

    filters: list[str] = []
    filter_params: list[Any] = []
    if category:
        filters.append("root_cause_category = %s")
        filter_params.append(category)
    if catalog_item:
        filters.append("catalog_item = %s")
        filter_params.append(catalog_item)
    where = f"WHERE {' AND '.join(filters)}" if filters else ""
    # Param order: SELECT distance, WHERE filters, ORDER BY distance, LIMIT
    params: list[Any] = [embedding, *filter_params, embedding, limit]

    with get_connection() as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT
                    id, job_id, root_cause_category, root_cause_summary,
                    catalog_item, confidence, analyzed_at, github_paths,
                    embedding <=> %s::vector AS distance
                FROM {table_name}
                {where}
                ORDER BY embedding <=> %s::vector
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for row in rows:
        github_paths = row[7]
        if isinstance(github_paths, str):
            try:
                github_paths = json.loads(github_paths)
            except json.JSONDecodeError:
                github_paths = []
        results.append(
            {
                "id": row[0],
                "job_id": row[1],
                "root_cause_category": row[2],
                "root_cause_summary": row[3],
                "catalog_item": row[4],
                "confidence": row[5],
                "analyzed_at": row[6],
                "github_paths": github_paths or [],
                "distance": float(row[8]),
            }
        )
    return results
