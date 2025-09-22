import os
import logging
from fastapi import FastAPI, Request
from pydantic import BaseModel
from mercapi import Mercapi
import aiohttp
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from mercapi.requests import SearchRequestData
from zoneinfo import ZoneInfo

# ---------------- Logging 設定 ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    force=True
)
logger = logging.getLogger(__name__)

app = FastAPI()

# ---------------- LINE 配置 ----------------
FETCH_SINCE_MINUTES = int(os.getenv("FETCH_SINCE_MINUTES") or 10)
LINE_TOKEN = os.getenv("LINE_TOKEN") or "IZXRGHe2cGK69Yrhpfif+255qo2iQFG87X/hbblkEOkZl2kNsyBBJGJd43PzmRpx5uiRseir5bnkxpDKI+9fzJLVY3Qe4mKKMXlKouyTs/Epn0qHyMwMIBt9S6/UXW45tG7Uieg73nQ/8xQAzUJcGwdB04t89/1O/w1cDnyilFU="
MERCARI_KEYWORD = os.getenv("MERCARI_KEYWORD") or "オラフ スヌーピー ぬいぐるみ"
OVERLAP_MINUTES = 2

# ---------------- 時區處理 ----------------
USER_TZ = os.getenv("USER_TIMEZONE", "Asia/Taipei")

def to_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

def to_user_time(dt: datetime) -> datetime:
    dt = to_utc_aware(dt)
    return dt.astimezone(ZoneInfo(USER_TZ))

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

    start_time = to_user_time(datetime.now(timezone.utc) - timedelta(minutes=minutes))
    end_time = to_user_time(datetime.now(timezone.utc))
    summary_text = (
        f"📌 關鍵字: {keyword}\n"
        f"🕒 時間區間: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n"
        f"✨ 新商品總數: {len(items)}"
    )

    search_url = f"https://jp.mercari.com/search?keyword={quote(keyword)}&sort=created_time&order=desc"

    # summary bubble
    summary_bubble = {
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
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "button", "style": "primary", "color": "#1E90FF",
                 "action": {"type": "uri", "label": "🔍 Mercari 搜尋", "uri": search_url}}
            ]
        }
    }
    columns.append(summary_bubble)

    for item in items[:max_items]:
        updated_tw = to_user_time(item["updated"])
        updated_str = updated_tw.strftime("%Y-%m-%d %H:%M")
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
                    {"type": "text", "text": f"🕒 上架時間: {updated_str}", "size": "sm", "color": "#888888", "margin": "sm"}
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

    return {"messages": [{"type": "flex", "altText": "有新 Mercari 商品！", "contents": {"type": "carousel", "contents": columns}}]}

# ---------------- 抓取新商品 ----------------
async def check_new_items(keyword, since_minutes=60):
    m = Mercapi()
    results = await m.search(
        keyword,
        sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
        sort_order=SearchRequestData.SortOrder.ORDER_DESC,
    )
    new_items = []
    time_threshold = datetime.now(timezone.utc) - timedelta(minutes=since_minutes + OVERLAP_MINUTES) 
    logger.info(f"[DEBUG] Time threshold: {time_threshold}")
    for item in results.items:
        item_updated = to_utc_aware(item.updated)
        if item_updated < time_threshold:
            continue
            
        if getattr(item, "status", "") == "ITEM_STATUS_TRADING":
            continue
    
        new_items.append({
            "name": item.name,
            "price": item.price,
            "url": f"https://jp.mercari.com/item/{item.id_}",
            "thumbnail": item.thumbnails[0] if item.thumbnails else "",
            "updated": item_updated
        })

    logger.info(f"[DEBUG] New items: {len(new_items)}")
    
    if new_items:
        payload = build_flex_message(new_items, keyword, since_minutes)
        await send_broadcast_message(payload)

# ---------------- CRON Endpoint ----------------
@app.get("/cron")
async def cron_fetch():
    try:
        keyword = MERCARI_KEYWORD
        minutes = FETCH_SINCE_MINUTES
        await check_new_items(keyword, since_minutes=minutes)
        return {"status": "ok", "message": f"Fetched new items for keyword '{keyword}'"}
    except Exception as e:
        logger.error(f"[ERROR] Cron fetch failed: {e}")
        return {"status": "error", "message": str(e)}

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

            m = Mercapi()
            results = await m.search(
                keyword,
                sort_by=SearchRequestData.SortBy.SORT_CREATED_TIME,
                sort_order=SearchRequestData.SortOrder.ORDER_DESC
            )
            new_items = []
            time_threshold = datetime.now(timezone.utc) - timedelta(minutes=minutes)
            for item in results.items:
                item_updated = to_utc_aware(item.updated)
                if item_updated < time_threshold:
                    continue
                
                if getattr(item, "status", "") == "ITEM_STATUS_TRADING":
                    continue

                new_items.append({
                    "name": item.name,
                    "price": item.price,
                    "url": f"https://jp.mercari.com/item/{item.id_}",
                    "thumbnail": item.thumbnails[0] if item.thumbnails else "",
                    "updated": item_updated
                })

            logger.info(f"[DEBUG] New items: {len(new_items)}")

            reply_token = event.get("replyToken")
            if reply_token:
                if new_items:
                    payload = build_flex_message(new_items, keyword, minutes)
                    await send_reply_message(reply_token, payload["messages"])
                else:
                    # 沒有新商品就回覆提示
                    await send_reply_message(reply_token, [{
                        "type": "text",
                        "text": f"❌ 在 {minutes} 分鐘內沒有找到「{keyword}」的新商品。"
                    }])

    return {"status": "ok"}

# ---------------- 測試 Endpoint ----------------
@app.get("/")
async def hello():
    return {"status": "ok", "message": "HELLO"}
