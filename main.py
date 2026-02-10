import os
import httpx
import requests
import psycopg2
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# ======================================================
# CONFIG
# ======================================================
os.makedirs("media", exist_ok=True)

SUNO_API_KEY = os.getenv("SUNO_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
BASE_URL = os.getenv("BASE_URL", "https://music-ppt.onrender.com")

CALLBACK_URL = f"{BASE_URL}/callback"

SUNO_BASE = "https://api.kie.ai/api/v1"
GENERATE_URL = f"{SUNO_BASE}/generate"
STATUS_URL = f"{SUNO_BASE}/generate/record-info"
LYRICS_URL = f"{SUNO_BASE}/generate/get-timestamped-lyrics"
VIDEO_URL = f"{SUNO_BASE}/mp4/generate"

app = FastAPI(title="AI Music Full Backend", version="V4_FULL_PIPELINE")
app.mount("/media", StaticFiles(directory="media"), name="media")

# ======================================================
# AUTO CREATE TABLE
# ======================================================
@app.on_event("startup")
def init_db():
    if not DATABASE_URL:
        print("DATABASE_URL NOT SET")
        return
    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS songs (
            id SERIAL PRIMARY KEY,
            audio_task_id TEXT UNIQUE,
            video_task_id TEXT,
            title TEXT,
            audio_url TEXT,
            video_url TEXT,
            lyrics TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    cur.close()
    conn.close()
    print("DB READY")

# ======================================================
# MODELS
# ======================================================
class GenerateRequest(BaseModel):
    prompt: str
    title: Optional[str] = "Untitled"
    style: Optional[str] = None
    instrumental: bool = False

# ======================================================
# HELPERS
# ======================================================
def headers():
    if not SUNO_API_KEY:
        raise HTTPException(500, "SUNO_API_KEY not set")
    return {
        "Authorization": f"Bearer {SUNO_API_KEY}",
        "Content-Type": "application/json"
    }

def db():
    if not DATABASE_URL:
        raise HTTPException(500, "DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL)

def download_file(url: str, path: str):
    with requests.get(url, stream=True, timeout=180) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(8192):
                if chunk:
                    f.write(chunk)

# ======================================================
# ROOT
# ======================================================
@app.get("/")
def root():
    return {"status": "running", "version": "V4_FULL_PIPELINE"}

# ======================================================
# GENERATE (APK HIT INI)
# ======================================================
@app.post("/generate")
async def generate(payload: GenerateRequest):

    body = {
        "model": "V4_5",
        "input": {
            "prompt": payload.prompt,
            "title": payload.title,
            "style": payload.style,
            "instrumental": payload.instrumental
        },
        "callBackUrl": CALLBACK_URL
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(
            GENERATE_URL,
            headers=headers(),
            json=body
        )

    if res.status_code != 200:
        raise HTTPException(500, res.text)

    return res.json()

# ======================================================
# CALLBACK
# ======================================================
@app.post("/callback")
async def callback(request: Request):
    try:
        data = await request.json()
        print("CALLBACK:", data)

        task_id = data.get("taskId")
        raw = data.get("data")

        if isinstance(raw, list):
            item = raw[0] if raw else None
        elif isinstance(raw, dict):
            item = raw
        else:
            return {"status": "ignored"}

        if not item:
            return {"status": "no_item"}

        state = item.get("state") or item.get("status")
        if state != "succeeded":
            return {"status": "processing"}

        audio_url = item.get("audioUrl") or item.get("streamAudioUrl")
        video_url = item.get("videoUrl") or item.get("resultUrl")

        conn = db()
        cur = conn.cursor()

        # ==================================================
        # AUDIO DONE
        # ==================================================
        if audio_url:

            audio_id = item.get("audioId")
            title = item.get("title", "Untitled")

            mp3_path = f"media/{task_id}.mp3"
            download_file(audio_url, mp3_path)
            local_audio = f"{BASE_URL}/media/{task_id}.mp3"

            # ===== GET LYRICS =====
            async with httpx.AsyncClient(timeout=60) as client:
                lyr = await client.post(
                    LYRICS_URL,
                    headers=headers(),
                    json={
                        "taskId": task_id,
                        "audioId": audio_id
                    }
                )

            lyrics_data = lyr.json()

            # ===== TRIGGER VIDEO =====
            async with httpx.AsyncClient(timeout=60) as client:
                vid = await client.post(
                    VIDEO_URL,
                    headers=headers(),
                    json={
                        "taskId": task_id,
                        "audioId": audio_id,
                        "callBackUrl": CALLBACK_URL,
                        "author": "AI Artist",
                        "domainName": BASE_URL
                    }
                )

            vid_json = vid.json()
            print("VIDEO START:", vid_json)

            video_task_id = None
            if vid_json.get("data"):
                video_task_id = vid_json["data"].get("taskId")

            cur.execute("""
                INSERT INTO songs 
                (audio_task_id, video_task_id, title, audio_url, lyrics, status)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT (audio_task_id)
                DO UPDATE SET
                    video_task_id=EXCLUDED.video_task_id,
                    audio_url=EXCLUDED.audio_url,
                    lyrics=EXCLUDED.lyrics,
                    status='audio_done'
            """, (
                task_id,
                video_task_id,
                title,
                local_audio,
                str(lyrics_data),
                "audio_done"
            ))

            conn.commit()
            cur.close()
            conn.close()

            return {"status": "audio_saved_video_started"}

        # ==================================================
        # VIDEO DONE
        # ==================================================
        if video_url:

            mp4_path = f"media/{task_id}.mp4"
            download_file(video_url, mp4_path)
            local_video = f"{BASE_URL}/media/{task_id}.mp4"

            cur.execute("""
                UPDATE songs
                SET video_url=%s,
                    status='done'
                WHERE video_task_id=%s
            """, (
                local_video,
                task_id
            ))

            conn.commit()
            cur.close()
            conn.close()

            return {"status": "video_saved"}

        cur.close()
        conn.close()
        return {"status": "unknown"}

    except Exception as e:
        print("CALLBACK ERROR:", str(e))
        return {"error": str(e)}

# ======================================================
# STATUS UNTUK APK
# ======================================================
@app.get("/status/{audio_task_id}")
def status(audio_task_id: str):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT title, audio_url, video_url, lyrics, status
        FROM songs
        WHERE audio_task_id=%s
    """, (audio_task_id,))

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
