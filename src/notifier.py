"""
Slack notifications. Silently no-ops if env vars are not set.

    notify(text) -> str | None   post a message, return thread_ts
    notify_thread(thread_ts, text) -> None   reply in thread

Requires env vars: SLACK_BOT_TOKEN, SLACK_NOTIFY_CHANNEL
"""
import os
import logging
import subprocess

logger = logging.getLogger(__name__)


def _cc_send(text: str) -> None:
    try:
        subprocess.run(["cc-connect", "send", "--message", text], timeout=10, check=False)
    except Exception as e:
        logger.warning(f"cc-connect send failed: {e}")

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


def notify(text: str) -> str | None:
    """Post a message to the configured Slack channel. Returns the message ts, or None."""
    channel = os.environ.get("SLACK_NOTIFY_CHANNEL")
    if not channel:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        resp = client.chat_postMessage(channel=channel, text=text)
        _cc_send(text)
        return resp["ts"]
    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
        return None


def notify_thread(thread_ts: str, text: str) -> None:
    """Reply to an existing message in a thread."""
    channel = os.environ.get("SLACK_NOTIFY_CHANNEL")
    if not channel or not thread_ts:
        return
    client = _get_client()
    if not client:
        return
    try:
        client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
    except Exception as e:
        logger.warning(f"Slack thread notification failed: {e}")
