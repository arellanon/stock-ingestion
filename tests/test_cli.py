from typer.testing import CliRunner
from stock_predictor.ingestion.cli import app
from stock_predictor.settings import Settings
import pytest

def test_all_commands_help():
    runner = CliRunner()
    for command in ("backfill","update","status","inspect","export","validate","enable","map-identifier"):
        result = runner.invoke(app,[command,"--help"])
        assert result.exit_code==0, result.output

def test_settings_reject_invalid_values():
    with pytest.raises(ValueError): Settings(batch_size=0)
    with pytest.raises(ValueError): Settings(intraday_intervals="2m")
