"""
Slack + Discord notifications. Silently no-ops if env vars are not set.

    notify(text) -> str | None   post a message, return thread_ts
    notify_thread(thread_ts, text) -> None   reply in thread

Requires env vars: SLACK_BOT_TOKEN, SLACK_NOTIFY_CHANNEL
Optional env vars: DISCORD_BOT_TOKEN, DISCORD_EARNINGS_CHANNEL
"""
import json
import os
import logging
import urllib.request

logger = logging.getLogger(__name__)

_DISCORD_CHANNEL = os.environ.get("DISCORD_EARNINGS_CHANNEL", "1513381046748581909")


def _discord_send(text: str) -> None:
    token = os.environ.get("DISCORD_BOT_TOKEN")
    if not token:
        return
    try:
        payload = json.dumps({"content": text}).encode()
        req = urllib.request.Request(
            f"https://discord.com/api/v10/channels/{_DISCORD_CHANNEL}/messages",
            data=payload,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://github.com/jvalansi/earnings-trader, 1.0)",
            },
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning(f"Discord notification failed: {e}")

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
    """Post a message to Discord and/or Slack. Returns Slack message ts, or None."""
    _discord_send(text)
    channel = os.environ.get("SLACK_NOTIFY_CHANNEL")
    if not channel:
        return None
    client = _get_client()
    if not client:
        return None
    try:
        resp = client.chat_postMessage(channel=channel, text=text)
        return resp["ts"]
    except Exception as e:
        logger.warning(f"Slack notification failed: {e}")
        return None


def notify_thread(thread_ts: str, text: str) -> None:
    """Reply to an existing message in a thread (Slack only)."""
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
