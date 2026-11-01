import hashlib
import hmac
import json
import os
import sqlite3
import time
from urllib.parse import parse_qsl

import httpx
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

BOT_TOKEN = os.environ["BOT_TOKEN"]
OWNER_ID = int(os.environ.get("OWNER_ID", "7752398574"))

app = FastAPI()
app.mount("/app/static", StaticFiles(directory="static"), name="static")

db = sqlite3.connect("saved.db", check_same_thread=False)

_avatar_cache: dict[int, tuple[float, bytes, str]] = {}
AVATAR_TTL = 3600


def validate_init_data(init_data: str) -> dict | None:
    try:
        parsed = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = parsed.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        return None
    auth_date = int(parsed.get("auth_date", 0))
    if time.time() - auth_date > 86400:
        return None
    return parsed


def check_owner(x_init_data: str | None) -> None:
    if not x_init_data:
        raise HTTPException(401, "no init data")
    data = validate_init_data(x_init_data)
    if not data:
        raise HTTPException(401, "invalid init data")
    user = json.loads(data.get("user", "{}"))
    if user.get("id") != OWNER_ID:
        raise HTTPException(403, "not the owner")


@app.get("/app", response_class=HTMLResponse)
def app_index():
    with open("static/index.html", encoding="utf-8") as f:
        return f.read()


@app.get("/api/chats")
def chats(x_init_data: str | None = Header(None)):
    check_owner(x_init_data)
    rows = db.execute("""
        SELECT
            chat_id,
            (SELECT name FROM messages m2
             WHERE m2.chat_id = messages.chat_id AND m2.is_owner = 0
             ORDER BY date DESC LIMIT 1) AS name,
            (SELECT username FROM messages m2
             WHERE m2.chat_id = messages.chat_id AND m2.is_owner = 0
             ORDER BY date DESC LIMIT 1) AS username,
            MAX(date) AS last_date,
            (SELECT text FROM messages m3
             WHERE m3.chat_id = messages.chat_id
             ORDER BY date DESC LIMIT 1) AS last_text,
            (SELECT mtype FROM messages m4
             WHERE m4.chat_id = messages.chat_id
             ORDER BY date DESC LIMIT 1) AS last_mtype,
            COUNT(*) AS total
        FROM messages
        GROUP BY chat_id
        ORDER BY last_date DESC
    """).fetchall()

    media_labels = {
        "photo": "🖼 Фото", "voice": "🎤 Голосовое", "video_note": "⭕ Кружок",
        "video": "📹 Видео", "animation": "🎞 GIF", "audio": "🎵 Аудио",
        "document": "📄 Файл", "sticker": "🩹 Стикер",
    }

    result = []
    for chat_id, name, username, last_date, last_text, last_mtype, total in rows:
        preview = last_text or media_labels.get(last_mtype, "")
        result.append({
            "chat_id": chat_id,
            "name": name or "Без имени",
            "username": username,
            "last_date": last_date,
            "last_text": preview,
            "total": total,
        })
    return JSONResponse(result)


@app.get("/api/messages")
def messages(chat_id: int = Query(...), x_init_data: str | None = Header(None)):
    check_owner(x_init_data)
    rows = db.execute("""
        SELECT msg_id, user_id, name, username, text, mtype, file_id,
               date, is_owner, deleted, deleted_at, edited
        FROM messages WHERE chat_id=? ORDER BY date ASC
    """, (chat_id,)).fetchall()

    result = []
    for r in rows:
        msg_id = r[0]
        item = {
            "msg_id": msg_id, "user_id": r[1], "name": r[2], "username": r[3],
            "text": r[4], "mtype": r[5], "has_media": bool(r[6]),
            "date": r[7], "is_owner": bool(r[8]), "deleted": bool(r[9]),
            "deleted_at": r[10], "edited": bool(r[11]),
        }
        if item["edited"]:
            edits = db.execute(
                "SELECT old_text, new_text, edited_at FROM edits "
                "WHERE chat_id=? AND msg_id=? ORDER BY edited_at ASC",
                (chat_id, msg_id),
            ).fetchall()
            item["edit_history"] = [
                {"old_text": e[0], "new_text": e[1], "edited_at": e[2]} for e in edits
            ]
        result.append(item)
    return JSONResponse(result)


@app.get("/api/file")
async def get_file(chat_id: int, msg_id: int, x_init_data: str | None = Header(None)):
    check_owner(x_init_data)
    row = db.execute(
        "SELECT file_id, mtype FROM messages WHERE chat_id=? AND msg_id=?",
        (chat_id, msg_id),
    ).fetchone()
    if not row or not row[0]:
        raise HTTPException(404, "file not found")
    file_id, mtype = row

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
        )
        data = r.json()
        if not data.get("ok"):
            raise HTTPException(410, "file no longer available on Telegram servers")
        file_path = data["result"]["file_path"]
        file_resp = await client.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}")
        if file_resp.status_code != 200:
            raise HTTPException(410, "file expired")

    content_type = {
        "photo": "image/jpeg", "voice": "audio/ogg", "video_note": "video/mp4",
        "video": "video/mp4", "animation": "video/mp4", "audio": "audio/mpeg",
        "sticker": "image/webp",
    }.get(mtype, "application/octet-stream")

    return Response(content=file_resp.content, media_type=content_type)


@app.get("/api/avatar")
async def get_avatar(chat_id: int, x_init_data: str | None = Header(None)):
    check_owner(x_init_data)

    cached = _avatar_cache.get(chat_id)
    if cached and time.time() - cached[0] < AVATAR_TTL:
        return Response(content=cached[1], media_type=cached[2])

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUserProfilePhotos",
            params={"user_id": chat_id, "limit": 1},
        )
        data = r.json()
        photos = data.get("result", {}).get("photos") if data.get("ok") else None
        if not photos:
            raise HTTPException(404, "no avatar")

        file_id = photos[0][-1]["file_id"]
        gf = (await client.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": file_id},
        )).json()
        if not gf.get("ok"):
            raise HTTPException(404, "file error")

        img = await client.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{gf['result']['file_path']}"
        )
        if img.status_code != 200:
            raise HTTPException(404, "download error")

    content = img.content
    _avatar_cache[chat_id] = (time.time(), content, "image/jpeg")
    return Response(content=content, media_type="image/jpeg")
