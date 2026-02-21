from unittest.mock import patch, MagicMock
import notifier


def _reset_client():
    notifier._client = None


# --- notify ---

def test_notify_no_channel_is_noop(monkeypatch):
    monkeypatch.delenv("SLACK_NOTIFY_CHANNEL", raising=False)
    mock_client = MagicMock()
    with patch("notifier._get_client", return_value=mock_client):
        notifier.notify("hello")
    mock_client.chat_postMessage.assert_not_called()


def test_notify_no_token_returns_no_client(monkeypatch):
    monkeypatch.setenv("SLACK_NOTIFY_CHANNEL", "C123")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    _reset_client()
    # _get_client returns None when no token → notify is a no-op
    with patch("notifier._get_client", return_value=None):
        notifier.notify("hello")  # should not raise


def test_notify_posts_to_configured_channel(monkeypatch):
    monkeypatch.setenv("SLACK_NOTIFY_CHANNEL", "C123")
    mock_client = MagicMock()
    with patch("notifier._get_client", return_value=mock_client):
        notifier.notify("test message")
    mock_client.chat_postMessage.assert_called_once_with(channel="C123", text="test message")


def test_notify_swallows_slack_errors(monkeypatch):
    monkeypatch.setenv("SLACK_NOTIFY_CHANNEL", "C123")
    mock_client = MagicMock()
    mock_client.chat_postMessage.side_effect = Exception("rate limited")
    with patch("notifier._get_client", return_value=mock_client):
        notifier.notify("test message")  # should not raise
