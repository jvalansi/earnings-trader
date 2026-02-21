"""
Slack notifications. Silently no-ops if env vars are not set.

    notify(text) -> None   post a message to SLACK_NOTIFY_CHANNEL

Requires env vars: SLACK_BOT_TOKEN, SLACK_NOTIFY_CHANNEL
"""
import os
import logging

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        try:
            from slack_sdk import WebClient
            token = os.environ.get("SLACK_BOT_TOKEN")
            if not token:
                return None
            _client = WebClient(token=token)
        except ImportError:
            logger.warning("slack_sdk not installed; Slack notifications disabled.")
            return None
    return _client


def notify(text: str) -> None:
    """Post a message to the configured Slack channel. Silently no-ops if unconfigured."""
    channel = os.environ.get("SLACK_NOTIFY_CHANNEL")
    if not channel:
        return
    client = _get_client()
    if not client:
        return
    try:
        client.chat_postMessage(channel=channel, text=text)
    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
