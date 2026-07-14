"""Best-effort Slack notifications. Never raises -- a notification failure
should never take down the graph run it's reporting on."""
from __future__ import annotations

import json
import os
import urllib.request


def notify(message: str) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        print(f"(no Slack webhook configured) {message}")
        return
    try:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps({"text": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"could not post to Slack: {exc}")
