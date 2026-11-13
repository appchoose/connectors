#
# Copyright Elasticsearch B.V. and/or licensed to Elasticsearch B.V. under one
# or more contributor license agreements. Licensed under the Elastic License 2.0;
# you may not use this file except in compliance with the Elastic License 2.0.
#
"""Pins the Choose fork's hook that hands the service config to data sources.

The PostgreSQL incremental window (``service.incremental_sync_interval_seconds``)
reaches the data source only through this hook.
"""

from tests.test_sync_job_runner import (  # noqa: F401 (autouse fixtures)
    create_runner,
    sync_orchestrator_mock,
)


async def test_runner_passes_service_config_to_data_source():
    service_config = {"incremental_sync_interval_seconds": 600}
    runner = create_runner(service_config=service_config)

    await runner.execute()

    runner.data_provider.set_service_config.assert_called_once_with(service_config)
