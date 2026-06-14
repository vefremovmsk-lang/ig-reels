#!/usr/bin/env python3
"""Publish a Reel to Instagram via the Graph API, with retries.

Env:   IG_USER_ID, META_TOKEN          (required)
       TG_BOT_TOKEN, TG_CHAT_ID        (optional -> Telegram ping)
Usage: python scripts/publish.py <video_url> <caption_file>
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

GRAPH = "https://graph.facebook.com/v23.0"
IG = os.environ["IG_USER_ID"]
TOKEN = os.environ["META_TOKEN"]


def notify(msg):
    print(msg)
    tok = os.environ.get("TG_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30
        )
    except Exception as e:
        print(f"(telegram notify failed: {e})")


def api(method, path, params, retries=4):
    """Call Graph API. Retries transient errors / RKN network flakiness."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            if method == "POST":
                req = urllib.request.Request(
                    f"{GRAPH}/{path}",
                    data=urllib.parse.urlencode(params).encode(),
                    method="POST",
                )
            else:
                req = urllib.request.Request(
                    f"{GRAPH}/{path}?{urllib.parse.urlencode(params)}", method="GET"
                )
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            last = f"HTTP {e.code}: {body}"
            transient = e.code >= 500
            try:
                err = json.loads(body).get("error", {})
                if err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 32, 341, 613):
                    transient = True
            except Exception:
                pass
            if attempt < retries and transient:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(last)
        except Exception as e:
            last = str(e)
            if attempt < retries:
                time.sleep(5 * attempt)
                continue
            raise RuntimeError(last)
    raise RuntimeError(last)


def main():
    video_url = sys.argv[1]
    caption = open(sys.argv[2], encoding="utf-8").read().strip()

    container = api("POST", f"{IG}/media", {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true",
        "access_token": TOKEN,
    })
    cid = container["id"]
    print(f"container {cid} created")

    status = None
    for _ in range(45):  # poll up to ~7.5 min while Meta transcodes
        st = api("GET", cid, {"fields": "status_code,status", "access_token": TOKEN})
        status = st.get("status_code")
        print(f"  status: {status}")
        if status == "FINISHED":
            break
        if status == "ERROR":
            raise RuntimeError(f"processing error: {st}")
        time.sleep(10)
    if status != "FINISHED":
        raise RuntimeError("container not FINISHED in time")

    pub = api("POST", f"{IG}/media_publish",
              {"creation_id": cid, "access_token": TOKEN})
    mid = pub["id"]
    link = api("GET", mid, {"fields": "permalink", "access_token": TOKEN}).get("permalink", "")
    notify(f"✅ Reel опубликован в @ai_findir\n{link}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        notify(f"❌ Публикация Reel не удалась: {e}")
        sys.exit(1)
