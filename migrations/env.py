from alembic import context
from sqlalchemy import create_engine
from stock_predictor.settings import Settings
from stock_predictor.adapters.schema import metadata
settings = Settings()
if context.is_offline_mode():
    context.configure(url=settings.database_url,target_metadata=metadata,literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(connection=connection,target_metadata=metadata)
        with context.begin_transaction(): context.run_migrations()
