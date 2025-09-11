#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
from ipaddress import (
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
)
from uuid import UUID

from asyncpg.exceptions._base import InternalClientError
from asyncpg.types import (
    BitString,
    Box,
    Circle,
    Line,
    LineSegment,
    Path,
    Point,
    Polygon,
)
from connectors_sdk.source import BaseDataSource
from connectors_sdk.utils import iso_utc
from sqlalchemy.exc import ProgrammingError

from connectors.sources.postgresql.client import PostgreSQLClient
from connectors.sources.postgresql.validator import PostgreSQLAdvancedRulesValidator
from connectors.sources.shared.database.generic_database import (
    DEFAULT_FETCH_SIZE,
    DEFAULT_RETRY_COUNT,
    hash_id,
    map_column_names,
)


class PostgreSQLDataSource(BaseDataSource):
    """PostgreSQL"""

    name = "PostgreSQL"
    service_type = "postgresql"
    advanced_rules_enabled = True
    incremental_sync_enabled = True

    def __init__(self, configuration):
        """Setup connection to the PostgreSQL database-server configured by user

        Args:
            configuration (DataSourceConfiguration): Instance of DataSourceConfiguration class.
        """
        super().__init__(configuration=configuration)
        self.database = self.configuration["database"]
        self.schema = self.configuration["schema"]
        # Initialize incremental sync interval with default value
        # Value will be updated by set_service_config() if available
        self._incremental_sync_interval_seconds = 120  # 2 minutes default
        self.postgresql_client = PostgreSQLClient(
            host=self.configuration["host"],
            port=self.configuration["port"],
            user=self.configuration["username"],
            password=self.configuration["password"],
            database=self.configuration["database"],
            schema=self.configuration["schema"],
            tables=self.configuration["tables"],
            ssl_enabled=self.configuration["ssl_enabled"],
            ssl_ca=self.configuration["ssl_ca"],
            retry_count=self.configuration["retry_count"],
            fetch_size=self.configuration["fetch_size"],
            logger_=self._logger,
        )

    def _set_internal_logger(self):
        self.postgresql_client.set_logger(self._logger)

    def set_service_config(self, service_config):
        """Configure service parameters for the connector.
        
        Args:
            service_config (dict): Service configuration
        """
        self._service_config = service_config

        self._incremental_sync_interval_seconds = service_config.get(
            'incremental_sync_interval_seconds', 120
        )
        self._logger.debug(
            f"Incremental sync interval set to {self._incremental_sync_interval_seconds} seconds"
        )
        
    @classmethod
    def get_default_configuration(cls):
        return {
            "host": {
                "label": "Host",
                "order": 1,
                "type": "str",
            },
            "port": {
                "display": "numeric",
                "label": "Port",
                "order": 2,
                "type": "int",
            },
            "username": {
                "label": "Username",
                "order": 3,
                "type": "str",
            },
            "password": {
                "label": "Password",
                "order": 4,
                "sensitive": True,
                "type": "str",
            },
            "database": {
                "label": "Database",
                "order": 5,
                "type": "str",
            },
            "schema": {
                "label": "Schema",
                "order": 6,
                "type": "str",
            },
            "tables": {
                "display": "textarea",
                "label": "Comma-separated list of tables",
                "options": [],
                "order": 7,
                "tooltip": "This configurable field is ignored when Advanced Sync Rules are used.",
                "type": "list",
                "value": "*",
            },
            "fetch_size": {
                "default_value": DEFAULT_FETCH_SIZE,
                "display": "numeric",
                "label": "Rows fetched per request",
                "order": 8,
                "required": False,
                "type": "int",
                "ui_restrictions": ["advanced"],
            },
            "retry_count": {
                "default_value": DEFAULT_RETRY_COUNT,
                "display": "numeric",
                "label": "Retries per request",
                "order": 9,
                "required": False,
                "type": "int",
                "ui_restrictions": ["advanced"],
            },
            "ssl_enabled": {
                "display": "toggle",
                "label": "Enable SSL verification",
                "order": 10,
                "type": "bool",
                "value": False,
            },
            "ssl_ca": {
                "depends_on": [{"field": "ssl_enabled", "value": True}],
                "label": "SSL certificate",
                "order": 11,
                "type": "str",
            },
        }

    def advanced_rules_validators(self):
        return [PostgreSQLAdvancedRulesValidator(self)]

    async def ping(self):
        """Verify the connection with the database-server configured by user"""
        self._logger.debug("Pinging the PostgreSQL instance")
        try:
            await self.postgresql_client.ping()
        except Exception as e:
            msg = f"Can't connect to Postgresql on {self.postgresql_client.host}."
            raise Exception(msg) from e

    def serialize(self, doc):
        """Override base serialize to handle PostgreSQL-specific types.

        PostgreSQL connector uses asyncpg which returns special Python objects for certain
        PostgreSQL data types that need to be serialized to strings:
        - Network types (INET, CIDR) -> ipaddress module objects
        - UUID type -> uuid.UUID objects
        - Geometric types (POINT, LINE, POLYGON, etc.) -> asyncpg.types objects
        - BitString type (BIT, VARBIT) -> asyncpg.types.BitString objects

        Args:
            doc (Dict): Dictionary to be serialized

        Returns:
            doc (Dict): Serialized version of dictionary
        """

        def _serialize(value):
            """Serialize input value with respect to its datatype.

            Args:
                value (Any): Value to be serialized

            Returns:
                value (Any): Serialized version of input value.
            """
            match value:
                case (
                    IPv4Address()
                    | IPv6Address()
                    | IPv4Interface()
                    | IPv6Interface()
                    | IPv4Network()
                    | IPv6Network()
                ):
                    return str(value)
                case UUID():
                    return str(value)
                case Point():
                    return f"({value.x}, {value.y})"
                case LineSegment():
                    return (
                        f"[({value.p1.x}, {value.p1.y}), ({value.p2.x}, {value.p2.y})]"
                    )
                case Box():
                    return f"[({value.high.x}, {value.high.y}), ({value.low.x}, {value.low.y})]"
                case Polygon():
                    # Polygon inherits from Path, so check it first
                    coords = [(p.x, p.y) for p in value.points]
                    return str(coords)
                case Path():
                    coords = [(p.x, p.y) for p in value.points]
                    status = "closed" if value.is_closed else "open"
                    return f"{status} {str(coords)}"
                case Line() | Circle():
                    return str(value)
                case BitString():
                    return value.as_string()
                case list() | tuple():
                    return [_serialize(item) for item in value]
                case dict():
                    return {k: _serialize(v) for k, v in value.items()}
                case _:
                    return value

        for key, value in doc.items():
            doc[key] = _serialize(value)

        return super().serialize(doc)

    def row2doc(self, row, doc_id, table, timestamp):
        row.update(
            {
                "_id": doc_id,
                "_timestamp": timestamp,
                "database": self.database,
                "table": table,
                "schema": self.schema,
            }
        )
        return row

    async def get_primary_key(self, tables):
        self._logger.debug(f"Extracting primary keys for tables: {tables}")
        primary_key_columns = []
        for table in tables:
            primary_key_columns.extend(
                await self.postgresql_client.get_table_primary_key(table)
            )

        return (
            map_column_names(
                column_names=primary_key_columns, schema=self.schema, tables=tables
            ),
            primary_key_columns,
        )

    async def fetch_documents_from_table(self, table):
        """Fetches all the table entries and format them in Elasticsearch documents

        Args:
            table (str): Name of table

        Yields:
            Dict: Document to be indexed
        """
        self._logger.info(f"Fetching records for table '{table}'")
        try:
            docs_generator = self._yield_all_docs_from_tables(table=table)
            async for doc in docs_generator:
                yield doc
        except (InternalClientError, ProgrammingError) as exception:
            self._logger.warning(
                f"Something went wrong while fetching document for table '{table}'. Error: {exception}"
            )

    async def fetch_documents_from_query(self, tables, query, id_columns, is_incremental=False):
        """Fetches all the data from the given query and format them in Elasticsearch documents

        Args:
            tables (str): List of tables
            query (str): Database Query
            id_columns (list): List of ID columns
            is_incremental (bool): Whether this is an incremental sync

        Yields:
            Dict: Document to be indexed
        """
        
        final_query = query
        
        if is_incremental:
            final_query = self._add_incremental_condition(query)    
        
        self._logger.info(
            f"Fetching records for {tables} tables using custom query: {final_query}"
        )

        try:
            docs_generator = self._yield_docs_custom_query(
                tables=tables, query=final_query, id_columns=id_columns, is_incremental=is_incremental
            )
            async for doc in docs_generator:
                yield doc
        except (InternalClientError, ProgrammingError) as exception:
            self._logger.warning(
                f"Something went wrong while fetching document for query '{query}' and tables {', '.join(tables)}. Error: {exception}"
            )

    async def _yield_docs_custom_query(self, tables, query, id_columns, is_incremental=False):
        
        primary_key_columns, _ = await self.get_primary_key(tables=tables)

        if id_columns:
            primary_key_columns = id_columns

        if not primary_key_columns:
            self._logger.warning(
                f"Skipping tables {', '.join(tables)} from database {self.database} since no primary key is associated with them. Assign primary key to the tables to index it in the next sync interval."
            )
            return

        async for row in self.yield_rows_for_query(
            primary_key_columns=primary_key_columns, tables=tables, query=query
        ):
            doc_id = f"{self.database}_{self.schema}_{hash_id(list(tables), row, primary_key_columns)}"

            yield self.serialize(
                doc=self.row2doc(
                    row=row,
                    doc_id=doc_id,
                    table=tables,
                    timestamp=iso_utc(),
                )
            )

    async def _yield_all_docs_from_tables(self, table):
        keys, order_by_columns = await self.get_primary_key(tables=[table])
        if keys:
            async for row in self.yield_rows_for_query(
                primary_key_columns=keys,
                tables=[table],
                order_by_columns=order_by_columns,
            ):
                doc_id = (
                    f"{self.database}_{self.schema}_{hash_id([table], row, keys)}"
                )
                yield self.serialize(
                    doc=self.row2doc(
                        row=row,
                        doc_id=doc_id,
                        table=table,
                        timestamp=iso_utc(),
                    )
                )
        else:
            self._logger.warning(
                f"Skipping table '{table}' from database '{self.database}' since no primary key is associated with it. Assign primary key to the table to index it in the next sync interval."
            )

    async def yield_rows_for_query(
        self,
        primary_key_columns,
        tables,
        query=None,
        row_count=None,
        order_by_columns=None,
    ):
        if query is None:
            streamer = self.postgresql_client.data_streamer(
                table=tables[0], row_count=row_count, order_by_columns=order_by_columns
            )
        else:
            streamer = self.postgresql_client.data_streamer(query=query)
        column_names = await anext(streamer)
        column_names = map_column_names(
            column_names=column_names, schema=self.schema, tables=tables
        )

        if not set(primary_key_columns) - set(column_names):
            async for row in streamer:
                row = dict(zip(column_names, row, strict=True))
                yield row
        else:
            self._logger.warning(
                f"Skipping query {query} for tables {', '.join(tables)} as primary key column or unique ID column name is not present in query."
            )

    async def get_docs(self, filtering=None):
        """Executes the logic to fetch databases, tables and rows in async manner.

        Yields:
            dictionary: Row dictionary containing meta-data of the row.
        """
        self._logger.info("Successfully connected to Postgresql.")
        if filtering and filtering.has_advanced_rules():
            advanced_rules = filtering.get_advanced_rules()
            self._logger.info(
                f"Fetching records from the database using advanced sync rules: {advanced_rules}"
            )
            for rule in advanced_rules:
                query = rule.get("query")
                tables = rule.get("tables")
                id_columns = rule.get("id_columns")
                if id_columns:
                    id_columns = map_column_names(
                        column_names=id_columns, schema=self.schema, tables=tables
                    )
                async for row in self.fetch_documents_from_query(
                    tables=tables, query=query, id_columns=id_columns, is_incremental=False
                ):
                    yield row, None

        else:
            table_count = 0

            async for table in self.postgresql_client.get_tables_to_fetch():
                table_count += 1
                async for row in self.fetch_documents_from_table(
                    table=table,
                ):
                    yield row, None

            if table_count < 1:
                self._logger.warning(
                    f"Fetched 0 tables for schema: {self.schema} and database: {self.database}"
                )

    def _add_incremental_condition(self, query):
        """Add the incremental condition to a SQL query.
        
        Args:
            query (str): The original SQL query
            
        Returns:
            str: The modified query with the incremental condition
        """
        if not query:
            return query
            
        # Clean the query by removing the semicolons at the end
        cleaned_query = query.rstrip(';').strip()
        
        # Get the configured interval
        interval_seconds = self._incremental_sync_interval_seconds
        
        # Check if the query contains a WHERE clause
        query_upper = cleaned_query.upper()
        if "WHERE" in query_upper:
            # Add the condition with AND
            return f"{cleaned_query} AND updated_at >= NOW() - INTERVAL '{interval_seconds} seconds'"
        else:
            # Add a new WHERE clause
            return f"{cleaned_query} WHERE updated_at >= NOW() - INTERVAL '{interval_seconds} seconds'"

    async def get_docs_incrementally(self, sync_cursor, filtering=None):
        """Executes the logic to fetch databases, tables and rows incrementally in async manner.

        Args:
            sync_cursor (dict): The sync cursor containing the last sync timestamp.
            filtering (optional): Advanced filtering rules. Defaults to None.

        Yields:
            tuple: (document, lazy_download, operation) where operation is 'index' for incremental sync.
        """
        self._logger.info("Successfully connected to Postgresql for incremental sync.")
        
        if filtering and filtering.has_advanced_rules():
            advanced_rules = filtering.get_advanced_rules()
            self._logger.info(
                f"Fetching records from the database using advanced sync rules (incremental): {advanced_rules}"
            )
            for rule in advanced_rules:
                query = rule.get("query")
                tables = rule.get("tables")
                id_columns = rule.get("id_columns")
                
                
                if id_columns:
                    id_columns = [
                        f"{self.schema}_{'_'.join(sorted(tables))}_{column}"
                        for column in id_columns
                    ]
                
                # Pass is_incremental=True to automatically add the incremental condition
                async for row in self.fetch_documents_from_query(
                    tables=tables, query=query, id_columns=id_columns, is_incremental=True
                ):
                    yield row, None, "index"
        else:
            table_count = 0
            async for table in self.postgresql_client.get_tables_to_fetch():
                table_count += 1
                async for row in self.fetch_documents_from_table_incremental(
                    table=table,
                ):
                    yield row, None, "index"

            if table_count < 1:
                self._logger.warning(
                    f"Fetched 0 tables for schema: {self.schema} and database: {self.database}"
                )

    async def fetch_documents_from_table_incremental(self, table):
        """Fetches table entries incrementally and format them in Elasticsearch documents

        Args:
            table (str): Name of table

        Yields:
            Dict: Document to be indexed
        """
        self._logger.info(f"Fetching records incrementally for table '{table}'")
        try:
            docs_generator = self._yield_all_docs_from_tables_incremental(table=table)
            async for doc in docs_generator:
                yield doc
        except (InternalClientError, ProgrammingError) as exception:
            self._logger.warning(
                f"Something went wrong while fetching document for table '{table}' (incremental). Error: {exception}"
            )

    async def _yield_all_docs_from_tables_incremental(self, table):
        """Yields documents from a table with incremental filtering based on updated_at timestamp."""
        keys, order_by_columns = await self.get_primary_key(tables=[table])
        if keys:
            async for row in self.yield_rows_for_query_incremental(
                primary_key_columns=keys,
                tables=[table],
                order_by_columns=order_by_columns,
            ):
                doc_id = (
                    f"{self.database}_{self.schema}_{hash_id([table], row, keys)}"
                )
                yield self.serialize(
                    doc=self.row2doc(
                        row=row,
                        doc_id=doc_id,
                        table=table,
                        timestamp=iso_utc(),
                    )
                )
        else:
            self._logger.warning(
                f"Skipping table '{table}' from database '{self.database}' since no primary key is associated with it. Assign primary key to the table to index it in the next sync interval."
            )

    async def yield_rows_for_query_incremental(
        self,
        primary_key_columns,
        tables,
        row_count=None,
        order_by_columns=None,
    ):
        """Yields rows from a query with incremental filtering for table-based queries."""
        # Get the configured interval
        interval_seconds = self._incremental_sync_interval_seconds
        
        # Build the query with the incremental condition
        order_by_clause = ",".join([f'"{column}"' for column in order_by_columns])
        incremental_query = f'SELECT * FROM "{self.schema}"."{tables[0]}" WHERE updated_at >= NOW() - INTERVAL \'{interval_seconds} seconds\' ORDER BY {order_by_clause} LIMIT {FETCH_LIMIT}'
        
        streamer = self.postgresql_client.data_streamer(query=incremental_query)
        column_names = await anext(streamer)
        column_names = map_column_names(
            column_names=column_names, schema=self.schema, tables=tables
        )

        if not set(primary_key_columns) - set(column_names):
            async for row in streamer:
                row = dict(zip(column_names, row, strict=True))
                yield row
        else:
            self._logger.warning(
                f"Skipping incremental query for table {tables[0]} as primary key column is not present in query."
            )
