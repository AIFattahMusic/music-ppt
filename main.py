import os
import httpx
import requests
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import uvicorn

# Buat direktori media
os.makedirs("media", exist_ok=True)

# Environment Variables
SUNO_API_KEY = os.getenv("SUNO_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://music-ppt.onrender.com")
CALLBACK_URL = f"{BASE_URL}/callback"

# Kie.ai API Endpoints
SUNO_BASE = "https://api.kie.ai/api/v1"
GENERATE_URL = f"{SUNO_BASE}/generate"
STATUS_URL = f"{SUNO_BASE}/generate/record-info"
LYRICS_URL = f"{SUNO_BASE}/generate/get-timestamped-lyrics"
VIDEO_URL = f"{SUNO_BASE}/mp4/generate"

app = FastAPI(
    title="MelodyMind AI Music Generator",
    description="Backend API for AI Music Generation",
    version="2.0.0"
)

# CORS Middleware - WAJIB untuk frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Bisa dibatasi ke domain spesifik di production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files untuk serve media
app.mount("/media", StaticFiles(directory="media"), name="media")

# ==================== MODELS ====================

class GenerateRequest(BaseModel):
    prompt: str
    style: Optional[str] = None
    title: Optional[str] = None
    instrumental: bool = False
    customMode: bool = False
    model: str = "V4_5"

class LyricsRequest(BaseModel):
    taskId: str
    audioId: Optional[str] = None

class VideoRequest(BaseModel):
    taskId: str
    audioId: Optional[str] = None
    callBackUrl: Optional[str] = None
    author: Optional[str] = "MelodyMind AI"
    domainName: Optional[str] = None

# ==================== HELPERS ====================

def get_headers():
    """Generate headers dengan API key"""
    if not SUNO_API_KEY:
        raise HTTPException(status_code=500, detail="SUNO_API_KEY not configured")
    return {
        "Authorization": f"Bearer {SUNO_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

def download_file(url: str, path: str):
    """Download file dari URL ke path lokal"""
    try:
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
        return True
    except Exception as e:
        print(f"Download error: {e}")
        return False

# ==================== ENDPOINTS ====================

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "status": "running",
        "service": "MelodyMind AI Music API",
        "version": "2.0.0",
        "endpoints": {
            "generate": "/generate",
            "check": "/check/{task_id}",
            "lyrics": "/lyrics",
            "video": "/video"
        }
    }

@app.get("/health")
async def health_check():
    """Detailed health check"""
    return {
        "status": "healthy",
        "api_key_configured": bool(SUNO_API_KEY),
        "base_url": BASE_URL,
        "callback_url": CALLBACK_URL
    }

# ==================== GENERATE ====================

@app.post("/generate")
async def generate_music(payload: GenerateRequest):
    """
    Generate musik baru dari prompt
    """
    body = {
        "prompt": payload.prompt,
        "style": payload.style,
        "title": payload.title,
        "instrumental": payload.instrumental,
        "customMode": payload.customMode,
        "model": payload.model,
        "callBackUrl": CALLBACK_URL
    }
    
    # Remove None values
    body = {k: v for k, v in body.items() if v is not None}
    
    print(f"Generating music with payload: {body}")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                GENERATE_URL,
                headers=get_headers(),
                json=body
            )
            response.raise_for_status()
            result = response.json()
            print(f"Generate response: {result}")
            return result
            
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Kie.ai API error: {e.response.text}"
        )
    except Exception as e:
        print(f"Generate error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== CHECK STATUS ====================

@app.get("/check/{task_id}")
async def check_status(task_id: str):
    """
    Cek status task generation
    """
    print(f"Checking status for task: {task_id}")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                STATUS_URL,
                headers=get_headers(),
                params={"taskId": task_id}
            )
            response.raise_for_status()
            result = response.json()
            print(f"Status response: {result}")
            return result
            
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Status check failed: {e.response.text}"
        )
    except Exception as e:
        print(f"Check error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== LYRICS ====================

@app.post("/lyrics")
async def get_lyrics(request: LyricsRequest):
    """
    Ambil lirik dengan timestamp untuk task tertentu
    """
    body = {
        "taskId": request.taskId
    }
    if request.audioId:
        body["audioId"] = request.audioId
    
    print(f"Fetching lyrics for task: {request.taskId}")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                LYRICS_URL,
                headers=get_headers(),
                json=body
            )
            response.raise_for_status()
            result = response.json()
            print(f"Lyrics response: {result}")
            return result
            
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Lyrics fetch failed: {e.response.text}"
        )
    except Exception as e:
        print(f"Lyrics error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== VIDEO ====================

@app.post("/video")
async def generate_video(request: VideoRequest):
    """
    Generate video MP4 dari audio yang sudah jadi
    """
    body = {
        "taskId": request.taskId,
        "callBackUrl": request.callBackUrl or CALLBACK_URL,
        "author": request.author,
        "domainName": request.domainName or BASE_URL
    }
    if request.audioId:
        body["audioId"] = request.audioId
    
    print(f"Generating video for task: {request.taskId}")
    
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                VIDEO_URL,
                headers=get_headers(),
                json=body
            )
            response.raise_for_status()
            result = response.json()
            print(f"Video response: {result}")
            return result
            
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.text}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Video generation failed: {e.response.text}"
        )
    except Exception as e:
        print(f"Video error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==================== CALLBACK ====================

@app.post("/callback")
async def webhook_callback(request: Request):
    """
    Webhook untuk menerima notifikasi dari Kie.ai
    Handle both audio completion dan video completion
    """
    try:
        data = await request.json()
        print(f"WEBHOOK RECEIVED: {data}")
        
        # ===== HANDLE VIDEO CALLBACK =====
        if data.get("code") == 0 and data.get("data", {}).get("video_url"):
            video_task_id = data["data"]["task_id"]
            video_url = data["data"]["video_url"]
            
            mp4_path = f"media/{video_task_id}.mp4"
            
            # Download video di background
            async def download_video():
                if download_file(video_url, mp4_path):
                    print(f"VIDEO SAVED: {mp4_path}")
                else:
                    print(f"VIDEO DOWNLOAD FAILED: {video_task_id}")
            
            asyncio.create_task(download_video())
            
            return {
                "status": "video_processing",
                "task_id": video_task_id,
                "message": "Video download started"
            }
        
        # ===== HANDLE AUDIO CALLBACK =====
        task_id = data.get("taskId") or data.get("task_id")
        raw_data = data.get("data")
        
        if not raw_data:
            return {"status": "ignored", "reason": "no_data"}
        
        # Parse data structure
        if isinstance(raw_data, list):
            item = raw_data[0] if raw_data else None
        elif isinstance(raw_data, dict):
            item = raw_data
        else:
            return {"status": "ignored", "reason": "invalid_data_format"}
        
        if not item:
            return {"status": "ignored", "reason": "empty_item"}
        
        state = item.get("state") or item.get("status")
        print(f"Task {task_id} state: {state}")
        
        if state != "succeeded":
            return {"status": "processing", "state": state}
        
        audio_url = item.get("audioUrl") or item.get("streamAudioUrl")
        
        if not audio_url:
            return {"status": "no_audio_url"}
        
        audio_id = item.get("audioId")
        
        # Download audio
        mp3_path = f"media/{task_id}.mp3"
        
        async def process_audio():
            if download_file(audio_url, mp3_path):
                print(f"AUDIO SAVED: {mp3_path}")
                
                # Trigger video generation otomatis
                if audio_id:
                    try:
                        async with httpx.AsyncClient() as client:
                            await client.post(
                                VIDEO_URL,
                                headers=get_headers(),
                                json={
                                    "taskId": task_id,
                                    "audioId": audio_id,
                                    "callBackUrl": CALLBACK_URL,
                                    "author": "MelodyMind AI",
                                    "domainName": BASE_URL
                                }
                            )
                            print(f"VIDEO GENERATION TRIGGERED: {task_id}")
                    except Exception as e:
                        print(f"Video trigger error: {e}")
            else:
                print(f"AUDIO DOWNLOAD FAILED: {task_id}")
        
        asyncio.create_task(process_audio())
        
        return {
            "status": "audio_processed",
            "task_id": task_id,
            "audio_id": audio_id,
            "path": mp3_path
        }
        
    except Exception as e:
        print(f"Callback error: {str(e)}")
        return {"status": "error", "message": str(e)}

# ==================== MEDIA ENDPOINTS ====================

@app.get("/media/list")
async def list_media():
    """List semua file media yang tersedia"""
    try:
        files = []
        for f in os.listdir("media"):
            path = os.path.join("media", f)
            if os.path.isfile(path):
                files.append({
                    "filename": f,
                    "size": os.path.getsize(path),
                    "url": f"{BASE_URL}/media/{f}"
                })
        return {"files": files}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ==================== MAIN ====================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
