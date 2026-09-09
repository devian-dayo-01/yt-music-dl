from __future__ import annotations

import asyncio
import html
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urljoin

import httpx
import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

INSTANCES_URL = "https://api.invidious.io/instances.json"
INSTANCE_REFRESH_SECONDS = int(os.getenv("INSTANCE_REFRESH_SECONDS", "900"))
HEALTHCHECK_CONCURRENCY = int(os.getenv("HEALTHCHECK_CONCURRENCY", "20"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "4.5"))
SEARCH_TIMEOUT = float(os.getenv("SEARCH_TIMEOUT", "6.0"))
PLAY_TIMEOUT = float(os.getenv("PLAY_TIMEOUT", "8.0"))
MAX_SEARCH_RESULTS = int(os.getenv("MAX_SEARCH_RESULTS", "12"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "120"))

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0 Safari/537.36",
)

app = FastAPI(title="Wave", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@dataclass
class Instance:
    uri: str
    score: float = 0.0
    latency_ms: float = 99999.0
    failures: int = 0
    successes: int = 0
    last_ok: float = 0.0
    last_error: float = 0.0
    api: bool = True
    cors: bool = False
    down: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return self.uri.rstrip("/")

    @property
    def key(self) -> str:
        return self.base

    def rank(self) -> float:
        # Lower is better. Reliability dominates raw latency.
        reliability_penalty = self.failures * 450
        success_bonus = min(self.successes, 20) * 8
        return self.latency_ms + reliability_penalty - success_bonus


class InstanceManager:
    def __init__(self) -> None:
        self.instances: dict[str, Instance] = {}
        self.lock = asyncio.Lock()
        self.last_refresh = 0.0
        self.search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
        self.video_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def refresh(self, force: bool = False) -> None:
        if not force and time.time() - self.last_refresh < INSTANCE_REFRESH_SECONDS and self.instances:
            return

        async with self.lock:
            if not force and time.time() - self.last_refresh < INSTANCE_REFRESH_SECONDS and self.instances:
                return

            try:
                async with httpx.AsyncClient(
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                ) as client:
                    response = await client.get(INSTANCES_URL)
                    response.raise_for_status()
                    raw = response.json()
            except Exception:
                # Keep the current working pool if the directory itself is unavailable.
                if self.instances:
                    return
                raw = []

            discovered: dict[str, Instance] = {}
            for item in raw:
                if not isinstance(item, list) or len(item) != 2:
                    continue
                host, meta = item
                if not isinstance(meta, dict):
                    continue

                uri = str(meta.get("uri") or "")
                if not uri:
                    uri = f"https://{host}"

                # Only use HTTPS public API instances.
                if not uri.startswith("https://"):
                    continue
                if meta.get("api") is False:
                    continue
                if meta.get("type") not in (None, "https"):
                    continue
                if meta.get("down") is True:
                    continue

                old = self.instances.get(uri.rstrip("/"))
                inst = old or Instance(uri=uri.rstrip("/"))
                inst.api = bool(meta.get("api", True))
                inst.cors = bool(meta.get("cors", False))
                inst.down = bool(meta.get("down", False))
                inst.metadata = meta
                discovered[inst.key] = inst

            self.instances = discovered
            self.last_refresh = time.time()

        await self.healthcheck()

    async def healthcheck(self) -> None:
        if not self.instances:
            return

        semaphore = asyncio.Semaphore(HEALTHCHECK_CONCURRENCY)

        async def check(inst: Instance) -> None:
            async with semaphore:
                started = time.perf_counter()
                try:
                    async with httpx.AsyncClient(
                        timeout=REQUEST_TIMEOUT,
                        follow_redirects=True,
                        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    ) as client:
                        # Lightweight endpoint; no YouTube request is necessary.
                        r = await client.get(f"{inst.base}/api/v1/stats")
                        if r.status_code >= 400:
                            raise RuntimeError(f"HTTP {r.status_code}")
                        data = r.json()
                        if not isinstance(data, dict):
                            raise RuntimeError("invalid stats response")

                    inst.latency_ms = (time.perf_counter() - started) * 1000
                    inst.successes += 1
                    inst.last_ok = time.time()
                    inst.failures = max(0, inst.failures - 1)
                except Exception:
                    inst.failures += 1
                    inst.last_error = time.time()
                    # Do not remove immediately: transient failures happen.
                    inst.latency_ms = min(inst.latency_ms * 1.25, 60000)

        await asyncio.gather(*(check(i) for i in self.instances.values()))
        self.instances = {
            k: v for k, v in self.instances.items()
            if v.failures < 4 or v.last_ok > time.time() - 3600
        }

    async def ranked(self) -> list[Instance]:
        await self.refresh()
        return sorted(self.instances.values(), key=lambda x: x.rank())

    async def request_json(self, path: str, *, timeout: float, limit: int = 0) -> tuple[dict[str, Any] | list[Any] | None, Instance | None]:
        candidates = await self.ranked()
        if limit:
            candidates = candidates[:limit]

        for inst in candidates:
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(
                    timeout=timeout,
                    follow_redirects=True,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                        "Referer": "https://www.youtube.com/",
                    },
                ) as client:
                    r = await client.get(urljoin(inst.base + "/", path.lstrip("/")))
                    if r.status_code >= 400:
                        raise RuntimeError(f"HTTP {r.status_code}")
                    data = r.json()

                inst.latency_ms = (time.perf_counter() - started) * 1000
                inst.successes += 1
                inst.last_ok = time.time()
                inst.failures = max(0, inst.failures - 1)
                return data, inst
            except Exception:
                inst.failures += 1
                inst.last_error = time.time()

        return None, None

    async def search(self, query: str) -> list[dict[str, Any]]:
        key = query.strip().lower()
        cached = self.search_cache.get(key)
        if cached and time.time() - cached[0] < CACHE_TTL:
            return cached[1]

        path = f"/api/v1/search?q={quote(query)}&type=video&sort_by=relevance&page=1"
        data, _ = await self.request_json(path, timeout=SEARCH_TIMEOUT)
        results: list[dict[str, Any]] = []

        if isinstance(data, list):
            for item in data[:MAX_SEARCH_RESULTS]:
                if not isinstance(item, dict):
                    continue
                video_id = item.get("videoId")
                if not video_id:
                    continue
                results.append(
                    {
                        "videoId": video_id,
                        "title": item.get("title") or "Untitled",
                        "author": item.get("author") or "",
                        "lengthSeconds": item.get("lengthSeconds") or 0,
                        "viewCount": item.get("viewCount") or 0,
                        "thumbnail": item.get("videoThumbnails", [{}])[0].get("url")
                        if item.get("videoThumbnails") else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                    }
                )

        if not results:
            results = await self.ytdlp_search(query)

        self.search_cache[key] = (time.time(), results)
        return results

    async def ytdlp_search(self, query: str) -> list[dict[str, Any]]:
        def run() -> list[dict[str, Any]]:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": True,
                "skip_download": True,
                "playlistend": MAX_SEARCH_RESULTS,
                "noplaylist": True,
                "user_agent": USER_AGENT,
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(f"ytsearch{MAX_SEARCH_RESULTS}:{query}", download=False)
                entries = info.get("entries") or []
                out = []
                for e in entries:
                    if not e or not e.get("id"):
                        continue
                    out.append(
                        {
                            "videoId": e["id"],
                            "title": e.get("title") or "Untitled",
                            "author": e.get("channel") or e.get("uploader") or "",
                            "lengthSeconds": e.get("duration") or 0,
                            "viewCount": e.get("view_count") or 0,
                            "thumbnail": e.get("thumbnail") or f"https://i.ytimg.com/vi/{e['id']}/hqdefault.jpg",
                        }
                    )
                return out
            except Exception:
                return []

        return await asyncio.to_thread(run)

    async def video(self, video_id: str) -> tuple[dict[str, Any] | None, Instance | None]:
        cached = self.video_cache.get(video_id)
        if cached and time.time() - cached[0] < 30:
            return cached[1], None

        path = f"/api/v1/videos/{quote(video_id)}"
        data, inst = await self.request_json(path, timeout=PLAY_TIMEOUT)

        if isinstance(data, dict):
            self.video_cache[video_id] = (time.time(), data)
            return data, inst

        fallback = await self.ytdlp_video(video_id)
        return fallback, None

    async def ytdlp_video(self, video_id: str) -> dict[str, Any] | None:
        def run() -> dict[str, Any] | None:
            opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "noplaylist": True,
                "user_agent": USER_AGENT,
                "referer": "https://www.youtube.com/",
                # Prefer audio. This avoids downloading video when this app is an audio player.
                "format": "bestaudio/best",
            }
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(
                        f"https://www.youtube.com/watch?v={video_id}",
                        download=False,
                    )
            except Exception:
                return None

        return await asyncio.to_thread(run)

    async def stream_info(self, video_id: str) -> dict[str, Any]:
        data, inst = await self.video(video_id)
        if not data:
            raise HTTPException(502, "再生情報を取得できませんでした。")

        # Invidious response.
        adaptive = data.get("adaptiveFormats") or []
        audio = [
            f for f in adaptive
            if f.get("url") and (
                str(f.get("type", "")).startswith("audio/")
                or (f.get("itag") in {139, 140, 141, 249, 250, 251, 171, 172})
            )
        ]

        # Prefer Opus/WebM, then M4A/AAC. Within codec choose higher bitrate.
        def audio_key(f: dict[str, Any]) -> tuple[int, int, int]:
            mime = str(f.get("type", ""))
            codec = str(f.get("codec", "")).lower()
            webm_opus = int("opus" in codec or "webm" in mime)
            bitrate = int(f.get("bitrate") or f.get("bitrateKbps") or 0)
            size = int(f.get("clen") or 0)
            return (webm_opus, bitrate, size)

        if audio:
            best = max(audio, key=audio_key)
            return {
                "videoId": video_id,
                "title": data.get("title") or "Untitled",
                "author": data.get("author") or "",
                "duration": data.get("lengthSeconds") or 0,
                "thumbnail": (data.get("videoThumbnails") or [{}])[0].get("url")
                or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "url": best["url"],
                "mime": best.get("type") or "audio/webm",
                "bitrate": best.get("bitrate") or 0,
                "source": inst.uri if inst else "invidious",
            }

        # yt-dlp fallback result.
        formats = data.get("formats") or []
        audio_formats = [
            f for f in formats
            if f.get("url") and (
                str(f.get("vcodec", "none")) == "none"
                or str(f.get("acodec", "none")) != "none"
            )
        ]
        if audio_formats:
            best = max(
                audio_formats,
                key=lambda f: (
                    int(f.get("abr") or 0),
                    int(f.get("tbr") or 0),
                    int(f.get("filesize") or 0),
                ),
            )
            return {
                "videoId": video_id,
                "title": data.get("title") or "Untitled",
                "author": data.get("uploader") or data.get("channel") or "",
                "duration": data.get("duration") or 0,
                "thumbnail": data.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
                "url": best["url"],
                "mime": best.get("mime") or "audio/mp4",
                "bitrate": best.get("abr") or best.get("tbr") or 0,
                "source": "yt-dlp",
            }

        raise HTTPException(502, "利用可能な音声ストリームが見つかりませんでした。")


manager = InstanceManager()


@app.on_event("startup")
async def startup() -> None:
    asyncio.create_task(manager.refresh(force=True))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/search")
async def api_search(query: str = Query(..., min_length=2, max_length=200)):
    results = await manager.search(query)
    return JSONResponse(results)


@app.get("/api/play/{video_id}")
async def api_play(video_id: str):
    if not video_id or len(video_id) > 32:
        raise HTTPException(400, "Invalid video ID")
    return JSONResponse(await manager.stream_info(video_id))


@app.get("/api/status")
async def api_status():
    instances = await manager.ranked()
    return {
        "count": len(instances),
        "healthy": sum(1 for i in instances if i.last_ok > i.last_error),
        "instances": [
            {
                "host": i.uri,
                "latency": round(i.latency_ms),
                "failures": i.failures,
                "successes": i.successes,
            }
            for i in instances[:25]
        ],
    }


@app.get("/health")
async def health():
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
