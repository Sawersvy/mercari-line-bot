import os
from fastapi import FastAPI, Request
from pydantic import BaseModel
from mercapi import Mercapi
import aiohttp
from datetime import datetime, timedelta

app = FastAPI()

# ---------------- LINE 配置 ----------------
CRON_SECRET = os.getenv("CRON_SECRET")
LINE_TOKEN = os.getenv("LINE_TOKEN")
LINE_USER_ID = os.getenv("LINE_USER_ID")
MERCARI_KEYWORD = os.getenv("MERCARI_KEYWORD")

# 記錄已推播商品
seen_items = set()


async def send_line_message(message_payload):
    """使用 LINE Messaging API 發送 Flex message"""
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=message_payload) as resp:
            resp_text = await resp.text()
            print("LINE 响應:", resp.status, resp_text)


def build_flex_message(items):
    """產生 Flex carousel message"""
    columns = []
    for item in items[:10]:  # 最多 10 個 bubble
        columns.append({
            "type": "bubble",
            "size": "kilo",
            "hero": {
                "type": "image",
                "url": item["thumbnail"],
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": item["name"][:40], "weight": "bold", "wrap": True},
                    {"type": "text", "text": f"價格: {item['price']}", "wrap": True}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "action": {"type": "uri", "label": "查看商品", "uri": item["url"]}}
                ]
            }
        })
    return {
        "to": LINE_USER_ID,
        "messages": [
            {
                "type": "flex",
                "altText": "有新 Mercari 商品！",
                "contents": {
                    "type": "carousel",
                    "contents": columns
                }
            }
        ]
    }


async def check_new_items(keyword, since_minutes=60):
    """抓取指定時間內的新商品"""
    global seen_items
    m = Mercapi()
    results = await m.search(keyword)
    new_items = []

    time_threshold = datetime.utcnow() - timedelta(minutes=since_minutes)

    for item in results.items:
        if item.id_ in seen_items:
            continue
        if item.created >= time_threshold:
            seen_items.add(item.id_)
            new_items.append({
                "name": item.name,
                "price": item.price,
                "url": f"https://jp.mercari.com/item/{item.id_}",
                "thumbnail": item.thumbnails[0] if item.thumbnails else ""
            })

    if new_items:
        payload = build_flex_message(new_items)
        await send_line_message(payload)


# ---------------- LINE Webhook ----------------
class LineEvent(BaseModel):
    type: str
    message: dict
    replyToken: str = None


@app.post("/webhook")
async def line_webhook(req: Request):
    """接收 LINE 指令"""
    body = await req.json()
    events = body.get("events", [])

    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].strip()
            keyword = MERCARI_KEYWORD
            minutes = 60  # 預設抓取 1 小時

            if text.startswith("今天"):
                minutes = 24 * 60
                keyword = text.replace("今天", "").strip() or keyword
            elif text.startswith("近一週"):
                minutes = 7 * 24 * 60
                keyword = text.replace("近一週", "").strip() or keyword
            elif text.startswith("近一個月"):
                minutes = 30 * 24 * 60
                keyword = text.replace("近一個月", "").strip() or keyword

            await check_new_items(keyword, since_minutes=minutes)
    return {"status": "ok"}


# ---------------- Vercel Cron Route ----------------
@app.get("/cron")
async def cron_job(keyword: str = MERCARI_KEYWORD, minutes: int = 60):
    
    """定時自動抓取，minutes 可調整抓取範圍"""
    await check_new_items(keyword, since_minutes=minutes)
    return {"status": "ok"}

@app.post("/")
async def hear_beat(req: Request):
    return {"status": "ok", "request": req}

@app.get("/")
async def hello():
    return "HELLO"
