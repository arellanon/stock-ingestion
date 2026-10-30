from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_backend: str = "postgresql"
    database_url: str = "postgresql+psycopg://stock_predictor:change_me@localhost/stock_predictor"
    market_data_provider: str = "yahoo"
    alpaca_api_key: str = ""
    alpaca_secret_key: str = ""
    alpaca_data_feed: str = "iex"
    ibkr_host: str = "127.0.0.1"
    ibkr_port: int = Field(7497, ge=1, le=65535)
    ibkr_client_id: int = Field(10, ge=0)
    ibkr_timeout_seconds: int = Field(45, ge=1, le=300)
    ibkr_exchange: str = "SMART"
    ibkr_currency: str = "USD"
    default_history_years: int = Field(5, ge=1, le=100)
    download_daily: bool = True
    download_intraday: bool = True
    intraday_intervals: str = "1m,5m,15m,60m"
    regular_session_only: bool = True
    batch_size: int = Field(1000, ge=1, le=10000)
    max_retries: int = Field(3, ge=0, le=10)
    retry_backoff_seconds: float = Field(2, ge=0)
    overlap_days: int = Field(7, ge=1)
    log_level: str = "INFO"
    tickers_file: str = "config/tickers.csv"
    @field_validator("intraday_intervals")
    @classmethod
    def intervals(cls, value):
        if any(x not in {"1m","5m","15m","60m"} for x in value.split(",")):
            raise ValueError("Invalid intraday interval")
        return value
