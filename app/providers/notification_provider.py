"""
app/providers/notification_provider.py — Optional ntfy.sh notifications.
"""

import requests

from app import config


def send_to_phone(report: str):
    if not config.NTFY_TOPIC:
        print("NTFY_TOPIC not set; skipping phone notification.", flush=True)
        return

    topic_url = f"https://ntfy.sh/{config.NTFY_TOPIC}"
    chunks = [report[i:i + 3900] for i in range(0, len(report), 3900)]

    for i, chunk in enumerate(chunks):
        title = "Daily Stock Report"  # no emoji — ntfy headers must be latin-1
        if len(chunks) > 1:
            title += f" ({i + 1}/{len(chunks)})"

        try:
            resp = requests.post(
                topic_url,
                data=chunk.encode("utf-8"),
                headers={
                    "Title": title,
                    "Priority": "default",
                    "Tags": "chart_with_upwards_trend",
                },
                timeout=10,
            )
            if resp.status_code == 200:
                print(f"Notification sent ({i + 1}/{len(chunks)})", flush=True)
            else:
                print(f"ntfy error: {resp.status_code} {resp.text}", flush=True)

        except Exception as e:
            print(f"Notification error: {e}", flush=True)
