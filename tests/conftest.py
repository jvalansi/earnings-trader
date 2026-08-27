import os
import sys
from pathlib import Path

# Allow imports from src/ without PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Never let a test reach a broker or inherit an operator halt. Set before config is
# imported so ALPACA_* from .env cannot leak into the test process — an earlier version
# of this suite submitted real orders to the Alpaca paper account.
os.environ["ALPACA_API_KEY"] = ""
os.environ["ALPACA_SECRET_KEY"] = ""
os.environ["LIVE_TRADING_CONFIRMED"] = "no"
os.environ["TRADING_HALTED"] = ""

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_runtime_files(tmp_path, monkeypatch):
    """Point every on-disk store at a temp dir so tests never touch data/."""
    import config, execution, risk, state, trade_history

    positions = tmp_path / "positions.json"
    trades = tmp_path / "trades_log.jsonl"

    monkeypatch.setattr(config, "POSITIONS_FILE", str(positions))
    monkeypatch.setattr(config, "TRADES_LOG_FILE", str(trades))
    monkeypatch.setattr(state, "_path", positions)
    monkeypatch.setattr(execution, "_log_path", trades)
    monkeypatch.setattr(trade_history, "TRADES_LOG_FILE", str(trades))
    monkeypatch.setattr(risk, "_state_path", tmp_path / "risk_state.json")
    monkeypatch.setattr(risk, "_halt_path", tmp_path / "HALT")


@pytest.fixture(autouse=True)
def no_broker(monkeypatch):
    """Default every test to simulation. Broker tests opt in by patching these back."""
    import execution
    monkeypatch.setattr(execution, "ALPACA_API_KEY", "")
    monkeypatch.setattr(execution, "ALPACA_SECRET_KEY", "")
