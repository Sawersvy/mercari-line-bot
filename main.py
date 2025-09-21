import asyncio
import os
import logging
from fastapi import FastAPI, Request
from pydantic import BaseModel
from mercapi import Mercapi
import aiohttp
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from mercapi.requests import SearchRequestData

# ---------------- Logging 設定 ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True  # 確保在 Vercel 重設 logging 設定
)
logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------- LINE 配置 ----------------
FETCH_INTERVAL_MINUTES = int(os.getenv("FETCH_INTERVAL_MINUTES") or 10)
FETCH_SINCE_MINUTES = 60
LINE_TOKEN = os.getenv("LINE_TOKEN") or "IZXRGHe2cGK69Yrhpfif+255qo2iQFG87X/hbblkEOkZl2kNsyBBJGJd43PzmRpx5uiRseir5bnkxpDKI+9fzJLVY3Qe4mKKMXlKouyTs/Epn0qHyMwMIBt9S6/UXW45tG7Uieg73nQ/8xQAzUJcGwdB04t89/1O/w1cDnyilFU="
MERCARI_KEYWORD = os.getenv("MERCARI_KEYWORD") or "オラフ スヌーピー ぬいぐるみ"

# 記錄已推播商品
seen_items = set()

# ---------------- 時區處理 ----------------
TW_TZ = timezone(timedelta(hours=8))

def to_utc_aware(dt: datetime) -> datetime:
    """將 naive datetime 視為 UTC 並轉成 aware"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def to_tw_time(dt: datetime) -> datetime:
    """將 datetime 轉成台灣時區"""
    dt = to_utc_aware(dt)
    return dt.astimezone(TW_TZ)

# ---------------- LINE 發送 ----------------
async def send_broadcast_message(message_payload):
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=message_payload) as resp:
            resp_text = await resp.text()
            logger.info(f"LINE Broadcast 響應: {resp.status} {resp_text}")

async def send_reply_message(reply_token, messages):
    url = "https://api.line.me/v2/bot/message/reply"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {"replyToken": reply_token, "messages": messages}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as resp:
            resp_text = await resp.text()
            logger.info(f"LINE Reply 響應: {resp.status} {resp_text}")

# ---------------- Flex Message ----------------
def build_flex_message(items, keyword, minutes, max_items=5):
    columns = []

    # Summary
    start_time = to_tw_time(datetime.now(timezone.utc) - timedelta(minutes=minutes))
    end_time = to_tw_time(datetime.now(timezone.utc))
    summary_text = (
        f"📌 關鍵字: {keyword}\n"
        f"🕒 時間區間: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"✨ 新商品總數: {len(items)}"
    )
    columns.append({
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": "🔔 Mercari 新商品通知", "weight": "bold", "size": "lg", "align": "center"},
                {"type": "separator", "margin": "md"},
                {"type": "text", "text": summary_text, "wrap": True, "margin": "md", "color": "#555555"}
            ]
        }
    })

    # 最新商品
    for item in items[:max_items]:
        created_tw = to_tw_time(item["created"])
        created_str = created_tw.strftime("%Y-%m-%d %H:%M")
        columns.append({
            "type": "bubble",
            "size": "kilo",
            "hero": {
                "type": "image",
                "url": item["thumbnail"] or "https://i.imgur.com/default.png",
                "size": "full",
                "aspectRatio": "20:13",
                "aspectMode": "cover"
            },
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "text", "text": item["name"][:40], "weight": "bold", "wrap": True, "size": "md"},
                    {
                        "type": "box",
                        "layout": "baseline",
                        "margin": "sm",
                        "contents": [
                            {"type": "text", "text": "💰 價格: ", "size": "sm", "color": "#888888"},
                            {"type": "text", "text": f"¥{item['price']}", "size": "sm", "weight": "bold", "color": "#FF5555"}
                        ]
                    },
                    {"type": "text", "text": f"🕒 上架時間: {created_str}", "size": "sm", "color": "#888888", "margin": "sm"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#00B900", "action": {"type": "uri", "label": "🔗 查看商品", "uri": item["url"]}}
                ]
            }
        })

    # 查看全部按鈕
    if len(items) > max_items:
        search_url = f"https://jp.mercari.com/search?keyword={quote(keyword)}&sort=created_time&order=desc"
        columns.append({
            "type": "bubble",
            "size": "kilo",
            "body": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "text", "text": "📄 查看更多商品", "weight": "bold", "size": "lg", "align": "center"},
                    {"type": "text", "text": "點擊下方按鈕前往 Mercari 查看完整列表", "wrap": True, "margin": "md", "color": "#555555"}
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "contents": [
                    {"type": "button", "style": "primary", "color": "#1E90FF", "action": {"type": "uri", "label": "查看更多", "uri": search_url}}
                ]
            }
        })

    return {"messages": [{"type": "flex", "altText": "有新 Mercari 商品！", "contents": {"type": "carousel", "contents": columns}}]}

# ---------------- 抓取新商品 ----------------
async def check_new_items(keyword, since_minutes=60):
    global seen_items
    m = Mercapi()
    results = await m.search(
        keyword,
        sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
        sort_order=SearchRequestData.SortOrder.ORDER_DESC
    )
    new_items = []

    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    logger.info(f"[DEBUG] Time threshold: {time_threshold}")

    for item in results.items:
        item_created = to_utc_aware(item.created)
        if item.id_ in seen_items or item_created < time_threshold:
            continue
        seen_items.add(item.id_)
        new_items.append({
            "name": item.name,
            "price": item.price,
            "url": f"https://jp.mercari.com/item/{item.id_}",
            "thumbnail": item.thumbnails[0] if item.thumbnails else "",
            "created": item_created
        })

    logger.info(f"[DEBUG] New items: {len(new_items)}")
    if new_items:
        payload = build_flex_message(new_items, keyword, since_minutes)
        await send_broadcast_message(payload)

# ---------------- Background Task ----------------
async def periodic_fetch():
    while True:
        try:
            keyword = os.getenv("MERCARI_KEYWORD") or MERCARI_KEYWORD
            minutes = int(os.getenv("FETCH_SINCE_MINUTES") or 60)
            await check_new_items(keyword, since_minutes=minutes)
        except Exception as e:
            logger.error(f"[ERROR] Background fetch failed: {e}")
        await asyncio.sleep(FETCH_INTERVAL_MINUTES * 60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(periodic_fetch())

# ---------------- LINE Webhook ----------------
class LineEvent(BaseModel):
    type: str
    message: dict
    replyToken: str = None

@app.post("/webhook")
async def line_webhook(req: Request):
    body = await req.json()
    events = body.get("events", [])

    for event in events:
        if event["type"] == "message" and event["message"]["type"] == "text":
            text = event["message"]["text"].strip()
            keyword = MERCARI_KEYWORD
            minutes = 60

            if text.startswith("今天"):
                minutes = 24*60
                keyword = text.replace("今天", "").strip() or keyword

            global seen_items
            m = Mercapi()
            results = await m.search(
                keyword,
                sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
                sort_order=SearchRequestData.SortOrder.ORDER_DESC
            )
            new_items = []

            time_threshold = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            for item in results.items:
                item_created = to_utc_aware(item.created)
                if item.id_ in seen_items or item_created < time_threshold:
                    continue
                seen_items.add(item.id_)
                new_items.append({
                    "name": item.name,
                    "price": item.price,
                    "url": f"https://jp.mercari.com/item/{item.id_}",
                    "thumbnail": item.thumbnails[0] if item.thumbnails else "",
                    "created": item_created
                })

            if new_items:
                payload = build_flex_message(new_items, keyword, minutes)
                reply_token = event.get("replyToken")
                if reply_token:
                    await send_reply_message(reply_token, payload["messages"])

    return {"status": "ok"}

# ---------------- 測試 Endpoint ----------------
@app.get("/")
async def hello():
    return {"status": "ok", "message": "HELLO"}
