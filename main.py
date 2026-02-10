import os
import httpx
import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# ================= CONFIG =================
os.makedirs("media", exist_ok=True)

SUNO_API_KEY = os.getenv("SUNO_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://music-ppt.onrender.com")

CALLBACK_URL = f"{BASE_URL}/callback"

SUNO_BASE = "https://api.kie.ai/api/v1"
GENERATE_URL = f"{SUNO_BASE}/generate"
LYRICS_URL = f"{SUNO_BASE}/generate/get-timestamped-lyrics"
VIDEO_URL = f"{SUNO_BASE}/mp4/generate"

app = FastAPI(title="Suno Full Pipeline FINAL")
app.mount("/media", StaticFiles(directory="media"), name="media")

# ================= MODEL =================
class GenerateRequest(BaseModel):
    prompt: str
    style: Optional[str] = None
    title: Optional[str] = None
    instrumental: bool = False
    customMode: bool = False
    model: str = "V4_5"

# ================= HELPERS =================
def headers():
    if not SUNO_API_KEY:
        raise HTTPException(500, "SUNO_API_KEY not set")
    return {
        "Authorization": f"Bearer {SUNO_API_KEY}",
        "Content-Type": "application/json"
    }

def download(url, path):
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

# ================= GENERATE =================
@app.post("/generate")
async def generate(payload: GenerateRequest):

    body = {
        "prompt": payload.prompt,
        "style": payload.style,
        "title": payload.title,
        "instrumental": payload.instrumental,
        "customMode": payload.customMode,
        "model": payload.model,
        "callBackUrl": CALLBACK_URL
    }

    async with httpx.AsyncClient(timeout=60) as client:
        res = await client.post(GENERATE_URL, headers=headers(), json=body)

    return res.json()

# ================= CALLBACK =================
@app.post("/callback")
async def callback(request: Request):

    data = await request.json()
    print("CALLBACK:", data)

    # ===================================================
    # ===== 1️⃣ HANDLE MP4 CALLBACK (FORMAT BARU DOCS)
    # ===================================================
    if data.get("code") == 0 and data.get("data", {}).get("video_url"):

        video_task_id = data["data"]["task_id"]
        video_url = data["data"]["video_url"]

        mp4_path = f"media/{video_task_id}.mp4"
        download(video_url, mp4_path)

        print("VIDEO SAVED:", video_task_id)
        return {"status": "video_saved"}

    # ===================================================
    # ===== 2️⃣ HANDLE AUDIO CALLBACK (FORMAT LAMA)
    # ===================================================
    task_id = data.get("taskId") or data.get("task_id")
    raw = data.get("data")

    if isinstance(raw, list):
        item = raw[0] if raw else None
    elif isinstance(raw, dict):
        item = raw
    else:
        item = None

    if not item:
        return {"status": "ignored"}

    state = item.get("state") or item.get("status")
    if state != "succeeded":
        return {"status": "processing"}

    audio_url = item.get("audioUrl") or item.get("streamAudioUrl")

    if audio_url:

        audio_id = item.get("audioId")

        # ===== SAVE MP3 =====
        mp3_path = f"media/{task_id}.mp3"
        download(audio_url, mp3_path)
        print("AUDIO SAVED:", task_id)

        # ===================================================
        # ===== GET TIMESTAMPED LYRICS (SESUI DOCS)
        # ===================================================
        async with httpx.AsyncClient(timeout=60) as client:
            lyr = await client.post(
                LYRICS_URL,
                headers=headers(),
                json={
                    "taskId": task_id,
                    "audioId": audio_id
                }
            )

        lyrics_json = lyr.json()
        print("LYRICS RAW:", lyrics_json)

        aligned_words = []
        if lyrics_json.get("code") == 200:
            aligned_words = lyrics_json.get("data", {}).get("alignedWords", [])

        # SIMPAN LIRIK KE FILE JSON
        lyrics_path = f"media/{task_id}_lyrics.json"
        with open(lyrics_path, "w", encoding="utf-8") as f:
            f.write(str(aligned_words))

        print("LYRICS SAVED:", lyrics_path)

        # ===================================================
        # ===== TRIGGER MP4 GENERATION
        # ===================================================
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

        print("VIDEO START:", vid.json())

        return {"status": "audio_processed_video_started"}

    return {"status": "done"}
