import os
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from mercapi import Mercapi
import aiohttp
from datetime import datetime, timedelta

app = FastAPI()

# ---------------- LINE 配置 ----------------
CRON_SECRET = os.getenv("CRON_SECRET")
LINE_TOKEN = os.getenv("LINE_TOKEN") or "IZXRGHe2cGK69Yrhpfif+255qo2iQFG87X/hbblkEOkZl2kNsyBBJGJd43PzmRpx5uiRseir5bnkxpDKI+9fzJLVY3Qe4mKKMXlKouyTs/Epn0qHyMwMIBt9S6/UXW45tG7Uieg73nQ/8xQAzUJcGwdB04t89/1O/w1cDnyilFU="
MERCARI_KEYWORD = os.getenv("MERCARI_KEYWORD") or "オラフ スヌーピー ぬいぐるみ"

# 記錄已推播商品
seen_items = set()


async def send_broadcast_message(message_payload):
    """使用 LINE Broadcast API 發送訊息給所有好友"""
    url = "https://api.line.me/v2/bot/message/broadcast"
    headers = {
        "Authorization": f"Bearer {LINE_TOKEN}",
        "Content-Type": "application/json"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=message_payload) as resp:
            resp_text = await resp.text()
            print("LINE Broadcast 响應:", resp.status, resp_text)


def build_flex_message(items, keyword, minutes, max_items=5):
    """產生美觀的 Flex Message，最新 max_items 商品 + summary + 查看更多"""
    columns = []

    # 1️⃣ Summary Bubble
    start_time = datetime.utcnow() - timedelta(minutes=minutes)
    end_time = datetime.utcnow()
    summary_text = f"📌 關鍵字: {keyword}\n🕒 時間區間: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}\n✨ 新商品總數: {len(items)}"

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

    # 2️⃣ 最新商品 Bubble (最多 max_items 件)
    for item in items[:max_items]:
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
                            {"type": "text", "text": str(item['price']), "size": "sm", "weight": "bold", "color": "#FF5555"}
                        ]
                    }
                ]
            },
            "footer": {
                "type": "box",
                "layout": "vertical",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#00B900",
                        "action": {"type": "uri", "label": "🔗 查看商品", "uri": item["url"]}
                    }
                ]
            }
        })

    # 3️⃣ 查看全部按鈕 Bubble
    if len(items) > max_items:
        search_url = f"https://jp.mercari.com/search?keyword={keyword}"
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

    return {
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
    print(f"[DEBUG] Time threshold: {time_threshold}")

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

    print(f"[DEBUG] New items: {len(new_items)}")
    if new_items:
        payload = build_flex_message(new_items, keyword, since_minutes)
        await send_broadcast_message(payload)


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
async def cron_job(request: Request, keyword: str = MERCARI_KEYWORD, minutes: int = 60):
    # 驗證 CRON_SECRET
    secret = os.getenv("CRON_SECRET")
    auth_header = request.headers.get("Authorization")
    if not auth_header or auth_header != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    # 確認是 Vercel Cron 觸發
    if "x-vercel-cron" in request.headers:
        print("Triggered by Vercel Cron")

    # 執行抓取
    await check_new_items(keyword, since_minutes=minutes)
    return {"status": "ok"}


@app.post("/")
async def hear_beat(req: Request):
    return {"status": "ok", "request": req}


@app.get("/")
async def hello():
    return "HELLO"
