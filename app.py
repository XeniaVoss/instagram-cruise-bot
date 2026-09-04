"""
Instagram "keyword in comment -> auto DM" bot.

How it works:
1. Meta sends a webhook POST every time someone comments on your Instagram posts/reels.
2. We check if the comment text contains one of TRIGGER_KEYWORDS.
3. If yes, we call the Instagram "private replies" endpoint to send that person
   a DM with AUTO_REPLY_TEXT -- this works even if they've never messaged you before,
   as long as we reply within 7 days of their comment (Meta's rule, not ours).

You do NOT need to know how to code to run this -- just follow SETUP.md.
"""

import os
import logging
import requests
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("cruise-bot")

app = Flask(__name__)

# ---- Config (all loaded from environment variables, see .env.example) ----
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "change-me")
PAGE_ACCESS_TOKEN = os.environ.get("PAGE_ACCESS_TOKEN", "")
IG_BUSINESS_ACCOUNT_ID = os.environ.get("IG_BUSINESS_ACCOUNT_ID", "")

log.info(
    "PAGE_ACCESS_TOKEN loaded: length=%d start=%r end=%r",
    len(PAGE_ACCESS_TOKEN),
    PAGE_ACCESS_TOKEN[:10],
    PAGE_ACCESS_TOKEN[-10:],
)

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.instagram.com/{GRAPH_API_VERSION}"

# Keywords that trigger the auto-DM. Lowercased, comma-separated in env var.
TRIGGER_KEYWORDS = [
    k.strip().lower()
    for k in os.environ.get("TRIGGER_KEYWORDS", "круиз,cruise").split(",")
    if k.strip()
]

# The message that gets sent in the DM when someone comments a trigger word.
AUTO_REPLY_TEXT = os.environ.get(
    "AUTO_REPLY_TEXT",
    "Hi! I booked my cruise through this tour: https://www.trip.com/t/t2cj7oymqV2 "
    "The cruise I personally took was called Wonderful Pearl Cruise. "
    "But honestly, all the cruise ships are pretty similar, so you can just choose "
    "whichever one you like best from the options there \U0001F600",
)

# Optional: restrict the automation to specific post/reel IDs.
# Leave empty to react to comments on ANY of your posts.
TARGET_MEDIA_IDS = [
    m.strip()
    for m in os.environ.get("TARGET_MEDIA_IDS", "").split(",")
    if m.strip()
]


@app.route("/webhook", methods=["GET"])
def verify_webhook():
    """Meta calls this once, when you click 'Verify and Save' in the App Dashboard."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        log.info("Webhook verified successfully.")
        return challenge, 200

    log.warning("Webhook verification failed (bad token).")
    return "Verification failed", 403


@app.route("/webhook", methods=["POST"])
def handle_webhook():
    """Meta calls this every time there's a new comment (or other subscribed event)."""
    data = request.get_json(silent=True) or {}
    log.info("Incoming webhook: %s", data)

    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            try:
                handle_comment(change.get("value", {}))
            except Exception:
                # One bad comment shouldn't block the rest of the batch.
                log.exception("Error while handling a single comment")

    # Always return 200 fast, otherwise Meta will retry / eventually disable the webhook.
    return jsonify(status="ok"), 200


def handle_comment(value: dict):
    comment_id = value.get("id")
    comment_text = (value.get("text") or "").lower()
    media_id = (value.get("media") or {}).get("id")
    commenter = value.get("from", {}) or {}
    commenter_username = commenter.get("username", "unknown")

    if not comment_id or not comment_text:
        return

    # Ignore comments made by the account itself (e.g. your own replies).
    if commenter.get("id") == IG_BUSINESS_ACCOUNT_ID:
        return

    if TARGET_MEDIA_IDS and media_id not in TARGET_MEDIA_IDS:
        log.info("Comment on media %s ignored (not in TARGET_MEDIA_IDS).", media_id)
        return

    if not any(keyword in comment_text for keyword in TRIGGER_KEYWORDS):
        return

    log.info(
        "Trigger keyword matched in comment %s from @%s -> sending DM",
        comment_id,
        commenter_username,
    )
    send_private_reply(comment_id)


def send_private_reply(comment_id: str):
    """Sends a DM in reply to a specific comment, via the Instagram private_replies endpoint."""
    url = f"{GRAPH_BASE}/{comment_id}/private_replies"
    try:
        resp = requests.post(
            url,
            params={"access_token": PAGE_ACCESS_TOKEN},
            json={"message": AUTO_REPLY_TEXT},
            timeout=10,
        )
    except requests.RequestException:
        log.exception("Network error while sending DM for comment %s", comment_id)
        return

    if resp.status_code == 200:
        log.info("DM sent successfully for comment %s", comment_id)
    else:
        log.error(
            "Failed to send DM for comment %s: %s %s",
            comment_id,
            resp.status_code,
            resp.text,
        )


@app.route("/", methods=["GET"])
def health():
    return "Cruise bot is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
