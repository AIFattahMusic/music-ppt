import os
import httpx
import requests
import psycopg2
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# ==================================================
# INIT
# ==================================================
os.makedirs("media", exist_ok=True)

SUNO_API_KEY = os.getenv("SUNO_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

BASE_URL = os.getenv(
    "BASE_URL",
    "https://music-ppt.onrender.com"
)

CALLBACK_URL = f"{BASE_URL}/callback"

SUNO_BASE_API = "https://api.kie.ai/api/v1"
MUSIC_GENERATE_URL = f"{SUNO_BASE_API}/generate"
STATUS_URL = f"{SUNO_BASE_API}/generate/record-info"
LYRICS_URL = f"{SUNO_BASE_API}/generate/get-timestamped-lyrics"
VIDEO_URL = f"{SUNO_BASE_API}/mp4/generate"

app = FastAPI(
    title="AI Music Suno API Wrapper",
    version="2.0.0"
)

app.mount("/media", StaticFiles(directory="media"), name="media")

# ================= REQUEST MODEL =================
class GenerateMusicRequest(BaseModel):
    prompt: str
    style: Optional[str] = None
    title: Optional[str] = None
    instrumental: bool = False
    customMode: bool = False
    model: str = "V4_5"

# ================= HELPERS =================
def suno_headers():
    if not SUNO_API_KEY:
        raise HTTPException(500, "SUNO_API_KEY not set")
    return {
        "Authorization": f"Bearer {SUNO_API_KEY}",
        "Content-Type": "application/json"
    }

def get_conn():
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL)

def save_file(url: str, path: str):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

# ================= ROOT =================
@app.get("/")
def root():
    return {"status": "running"}

# ================= GENERATE MUSIC (TIDAK DIUBAH) =================
@app.post("/generate-music")
async def generate_music(payload: GenerateMusicRequest):

    body = {
        "prompt": payload.prompt,
        "customMode": payload.customMode,
        "instrumental": payload.instrumental,
        "model": payload.model,
        "callBackUrl": CALLBACK_URL
    }

    if payload.style:
        body["style"] = payload.style
    if payload.title:
        body["title"] = payload.title

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            MUSIC_GENERATE_URL,
            headers=suno_headers(),
            json=body
        )

    return res.json()

# ================= CALLBACK =================
@app.post("/callback")
async def callback(request: Request):
    try:
        data = await request.json()
        print("CALLBACK:", data)

        task_id = data.get("taskId") or data.get("task_id")
        items = data.get("data") or []

        if isinstance(items, list):
            item = items[0] if items else None
        elif isinstance(items, dict):
            item = items
        else:
            item = None

        if not item:
            return {"status": "ignored"}

        state = item.get("state") or item.get("status")
        if state != "succeeded":
            return {"status": "processing"}

        audio_url = (
            item.get("audioUrl")
            or item.get("audio_url")
            or item.get("streamAudioUrl")
        )

        video_url = (
            item.get("videoUrl")
            or item.get("video_url")
            or item.get("resultUrl")
        )

        conn = get_conn()
        cur = conn.cursor()

        # ================= AUDIO DONE =================
        if audio_url:

            audio_id = item.get("audioId")
            title = item.get("title", "Untitled")

            # SAVE MP3
            mp3_path = f"media/{task_id}.mp3"
            save_file(audio_url, mp3_path)
            local_audio_url = f"{BASE_URL}/media/{task_id}.mp3"

            # ===== TAMBAHAN: GET LYRICS =====
            async with httpx.AsyncClient(timeout=60) as client:
                lyrics_res = await client.post(
                    LYRICS_URL,
                    headers=suno_headers(),
                    json={
                        "taskId": task_id,
                        "audioId": audio_id
                    }
                )

            lyrics_data = lyrics_res.json()
            print("LYRICS:", lyrics_data)

            # ===== TAMBAHAN: TRIGGER VIDEO =====
            async with httpx.AsyncClient(timeout=60) as client:
                video_res = await client.post(
                    VIDEO_URL,
                    headers=suno_headers(),
                    json={
                        "taskId": task_id,
                        "audioId": audio_id,
                        "callBackUrl": CALLBACK_URL,
                        "author": "AI Artist",
                        "domainName": BASE_URL
                    }
                )

            video_json = video_res.json()
            print("VIDEO START:", video_json)

            video_task_id = None
            if video_json.get("data"):
                video_task_id = video_json["data"].get("taskId")

            # SAVE DB
            cur.execute("""
                INSERT INTO songs
                (task_id, title, audio_url, lyrics, video_task_id, status)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (task_id) DO UPDATE SET
                    audio_url=EXCLUDED.audio_url,
                    lyrics=EXCLUDED.lyrics,
                    video_task_id=EXCLUDED.video_task_id,
                    status='audio_done'
            """, (
                task_id,
                title,
                local_audio_url,
                str(lyrics_data),
                video_task_id,
                "audio_done"
            ))

            conn.commit()
            cur.close()
            conn.close()

            return {"status": "audio_saved_video_started"}

        # ================= VIDEO DONE =================
        if video_url:

            mp4_path = f"media/{task_id}.mp4"
            save_file(video_url, mp4_path)
            local_video_url = f"{BASE_URL}/media/{task_id}.mp4"

            cur.execute("""
                UPDATE songs
                SET video_url=%s,
                    status='done'
                WHERE video_task_id=%s
            """, (local_video_url, task_id))

            conn.commit()
            cur.close()
            conn.close()

            return {"status": "video_saved"}

        cur.close()
        conn.close()
        return {"status": "unknown"}

    except Exception as e:
        print("CALLBACK ERROR:", e)
        return {"status": "error", "error": str(e)}

# ================= STATUS UNTUK APK =================
@app.get("/status/{task_id}")
def status(task_id: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT title, audio_url, video_url, lyrics, status
        FROM songs
        WHERE task_id=%s
    """, (task_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return {"status": "not_found"}

    return {
        "title": row[0],
        "audio_url": row[1],
        "video_url": row[2],
        "lyrics": row[3],
        "status": row[4]
    }
