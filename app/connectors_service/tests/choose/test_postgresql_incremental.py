#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Pins the Choose fork's PostgreSQL incremental sync.

Incremental syncs re-read only the rows whose ``updated_at`` falls in a
sliding window (``service.incremental_sync_interval_seconds``, default 120s).
These tests record the SQL the data source sends to PostgreSQL and the
documents it yields, so an upstream upgrade that breaks the fork shows up
here instead of in production.

Tests named ``test_known_limitation_*`` pin current behavior that is
probably wrong; change them together with the fix.
"""

from unittest.mock import AsyncMock, Mock

import pytest
from connectors_sdk.filtering.validation import Filter

from connectors.sources.postgresql import PostgreSQLDataSource
from tests.sources.support import create_source

SCHEMA = "public"
TABLE = "emp_table"


class RecordingDatabase:
    """Stands in for PostgreSQLClient: records SQL, returns two rows."""

    def __init__(self):
        self.queries = []

    async def data_streamer(
        self, table=None, query=None, row_count=None, order_by_columns=None
    ):
        self.queries.append(query)
        yield ["ids", "names"]
        yield (1, "abcd")
        yield (2, "xyz")

    async def get_tables_to_fetch(self, is_filtering=False):
        yield TABLE

    async def get_table_primary_key(self, table):
        return ["ids"]


@pytest.fixture
async def source():
    async with create_source(
        PostgreSQLDataSource, database="xe", tables="*", schema=SCHEMA, port=5432
    ) as source:
        db = RecordingDatabase()
        source.postgresql_client.data_streamer = db.data_streamer
        source.postgresql_client.get_tables_to_fetch = db.get_tables_to_fetch
        source.postgresql_client.get_table_primary_key = db.get_table_primary_key
        source.db = db
        yield source


def advanced_rules(query, tables=(TABLE,)):
    return Filter(
        {"advanced_snippet": {"value": [{"tables": list(tables), "query": query}]}}
    )


async def incremental_docs(source, filtering=None):
    return [
        item
        async for item in source.get_docs_incrementally(
            sync_cursor=None, filtering=filtering
        )
    ]


def test_incremental_sync_is_enabled():
    assert PostgreSQLDataSource.incremental_sync_enabled is True


async def test_custom_query_reads_only_the_default_120s_window(source):
    items = await incremental_docs(source, advanced_rules("select * from emp_table;"))

    assert source.db.queries == [
        "select * from emp_table"
        " WHERE \"emp_table\".updated_at >= NOW() - INTERVAL '120 seconds'"
    ]
    assert [(doc["_id"], lazy, op) for doc, lazy, op in items] == [
        ("xe_public_emp_table_1", None, "index"),
        ("xe_public_emp_table_2", None, "index"),
    ]


async def test_custom_query_with_where_uses_configured_window(source):
    source.set_service_config({"incremental_sync_interval_seconds": 600})

    await incremental_docs(
        source, advanced_rules("SELECT * FROM emp_table WHERE names <> ''")
    )

    assert source.db.queries == [
        "SELECT * FROM emp_table WHERE names <> ''"
        " AND \"emp_table\".updated_at >= NOW() - INTERVAL '600 seconds'"
    ]


async def test_missing_interval_in_service_config_falls_back_to_120s(source):
    source.set_service_config({})

    await incremental_docs(source, advanced_rules("select * from emp_table"))

    assert source.db.queries[0].endswith("INTERVAL '120 seconds'")


async def test_full_sync_custom_query_is_not_windowed(source):
    docs = [
        doc
        async for doc, _ in source.get_docs(
            filtering=advanced_rules("select * from emp_table")
        )
    ]

    assert source.db.queries == ["select * from emp_table"]
    assert len(docs) == 2


async def test_known_bug_table_mode_incremental_sync_crashes(source):
    # No advanced rules: the incremental table query references FETCH_LIMIT,
    # which datasource.py never imports. Same on v9.4.4-choose.
    # Once fixed, note the query is a single "LIMIT FETCH_LIMIT" page.
    with pytest.raises(NameError, match="FETCH_LIMIT"):
        await incremental_docs(source)

    assert source.db.queries == []


async def test_known_limitation_window_is_appended_after_order_by(source):
    # The condition is appended at the end of the query: with ORDER BY/LIMIT
    # the resulting SQL is invalid.
    await incremental_docs(
        source, advanced_rules("select * from emp_table order by ids")
    )

    assert source.db.queries == [
        "select * from emp_table order by ids"
        " WHERE \"emp_table\".updated_at >= NOW() - INTERVAL '120 seconds'"
    ]


async def test_known_limitation_schema_qualified_from_targets_the_schema(source):
    # "FROM public.emp_table" is parsed as table "public".
    await incremental_docs(source, advanced_rules("select * from public.emp_table"))

    assert source.db.queries == [
        "select * from public.emp_table"
        " WHERE \"public\".updated_at >= NOW() - INTERVAL '120 seconds'"
    ]


async def test_close_disposes_the_engine():
    async with create_source(
        PostgreSQLDataSource, database="xe", tables="*", schema=SCHEMA, port=5432
    ) as source:
        engine = Mock(dispose=AsyncMock())
        source.postgresql_client.engine = engine

    engine.dispose.assert_awaited_once()
