from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os
import yt_dlp
import json
import time
from dotenv import load_dotenv

load_dotenv()

INSTANCES_URL = "https://api.invidious.io/instances.json"
MAX_AGE = 600  # 10分キャッシュ

app = FastAPI(title="YT Music 練習 - yt-dlp + Invidious")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class YTDLPClient:
    def __init__(self, instance=None):
        self.instance = instance
        opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'format': 'bestvideo[height<=720]+bestaudio/best',
            'skip_download': True,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'referer': 'https://youtube.com',
        }
        if instance:
            opts['proxy'] = f'http://{instance}'
        self.ydl = yt_dlp.YoutubeDL(opts)

    def get_video_ids(self, query: str):
        try:
            info = self.ydl.extract_info(f"ytsearch:{query}", download=False)
            entries = info.get('entries') or [info]
            return [{
                "title": e.get("title"),
                "videoId": e.get("id"),
                "thumbnail": e.get("thumbnails", [{}])[0].get("url")
            } for e in entries if e.get("id")]
        except:
            return []

def load_instances():
    try:
        r = yt_dlp.YoutubeDL({}).extract_info(INSTANCES_URL, download=False)
        return [i["uri"] for i in r.get("entries", []) if i.get("uri")]
    except:
        return ["inv.nadeko.net", "invidious.nerdvpn.de", "yewtu.be"]

active_instances = load_instances()
instance_index = 0
last_refresh = 0

def get_best_client():
    global instance_index, last_refresh
    now = time.time()
    if now - last_refresh > MAX_AGE:
        active_instances[:] = load_instances()
        last_refresh = now
        instance_index = 0
    client = YTDLPClient(active_instances[instance_index])
    instance_index = (instance_index + 1) % len(active_instances)
    return client

@app.get("/", response_class=HTMLResponse)
async def home():
    return templates.TemplateResponse("index.html", {"request": {}})

@app.get("/api/search")
async def search(query: str = Query(..., min_length=2)):
    client = get_best_client()
    return client.get_video_ids(query)

@app.get("/api/play/{video_id}")
async def play(video_id: str):
    client = get_best_client()
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestvideo[height<=720]+bestaudio/best',
        'skip_download': True,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'referer': 'https://youtube.com',
        'force_yt_dlp': True,
    }
    try:
        info = client.ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
        formats = info.get('formats') or []
        if not formats:
            raise HTTPException(500, "動画取得失敗")
        best = max(formats, key=lambda f: (f.get('height') or 0) * 1000000 + (f.get('filesize') or 0))
        video_url = best.get('url')
        player_html = f"""
        <video width="100%" controls autoplay>
            <source src="{video_url}" type="video/mp4">
            Your browser does not support the video tag.
        </video>
        """
        return {"video_id": video_id, "player": player_html}
    except Exception as e:
        raise HTTPException(500, str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
