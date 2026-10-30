import os
import pytest
import sqlalchemy as sa
from stock_predictor.adapters.schema import metadata
from stock_predictor.adapters.persistence import SQLUnitOfWork

@pytest.fixture(params=["sqlite","postgresql"])
def factory(request):
    if request.param=="postgresql":
        url = os.getenv("TEST_DATABASE_URL")
        if not url: pytest.skip("TEST_DATABASE_URL not set; real PostgreSQL contract not run")
        engine = sa.create_engine(url)
        # Tests own an isolated schema, never touch production public tables.
        import uuid
        name = "test_"+uuid.uuid4().hex
        with engine.begin() as connection: connection.exec_driver_sql(f'CREATE SCHEMA "{name}"')
        engine = engine.execution_options(schema_translate_map={None:name})
    else:
        engine = sa.create_engine("sqlite://")
        @sa.event.listens_for(engine,"connect")
        def enforce_fk(connection,record): connection.execute("PRAGMA foreign_keys=ON")
    metadata.create_all(engine)
    yield lambda: SQLUnitOfWork(engine,2)
    metadata.drop_all(engine)
    if request.param=="postgresql":
        with engine.begin() as connection: connection.exec_driver_sql(f'DROP SCHEMA "{name}"')
    engine.dispose()
