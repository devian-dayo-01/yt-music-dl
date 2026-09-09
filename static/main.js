const $ = (id) => document.getElementById(id);

let queue = [];
let currentIndex = -1;
let currentTrack = null;
let searchTimer = null;

const audio = new Audio();
audio.preload = "auto";

function escapeHtml(value) {
    return String(value ?? "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

function formatTime(seconds) {
    seconds = Number(seconds || 0);
    if (!Number.isFinite(seconds)) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60).toString().padStart(2, "0");
    return `${m}:${s}`;
}

async function search() {
    const query = $("searchInput").value.trim();
    if (!query) return;

    $("searchBtn").disabled = true;
    $("searchBtn").textContent = "検索中…";
    $("results").innerHTML = `<div class="loading"><span></span><span></span><span></span></div>`;

    try {
        const res = await fetch(`/api/search?query=${encodeURIComponent(query)}`);
        if (!res.ok) throw new Error("検索に失敗しました");
        const videos = await res.json();

        if (!videos.length) {
            $("results").innerHTML = `<div class="empty">見つかりませんでした</div>`;
            return;
        }

        $("results").innerHTML = videos.map((v, i) => `
            <article class="track-card">
                <button class="thumb-button" onclick="playIndex(${i})" aria-label="再生">
                    <img src="${escapeHtml(v.thumbnail)}" alt="" loading="lazy">
                    <span class="play-overlay">▶</span>
                </button>
                <div class="track-info">
                    <div class="track-title">${escapeHtml(v.title)}</div>
                    <div class="track-meta">${escapeHtml(v.author || "Unknown")} · ${formatTime(v.lengthSeconds)}</div>
                </div>
                <button class="add-btn" onclick="addIndex(${i})" aria-label="キューに追加">＋</button>
            </article>
        `).join("");

        window.lastResults = videos;
    } catch (err) {
        $("results").innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    } finally {
        $("searchBtn").disabled = false;
        $("searchBtn").textContent = "検索";
    }
}

async function playIndex(index) {
    const item = window.lastResults?.[index];
    if (!item) return;
    await playTrack(item);
}

function addIndex(index) {
    const item = window.lastResults?.[index];
    if (!item) return;
    queue.push(item);
    renderQueue();
    if (currentIndex === -1) playTrack(item);
}

async function playTrack(track) {
    currentTrack = track;
    setPlayer(track, true);
    $("playerStatus").textContent = "接続中…";

    try {
        const res = await fetch(`/api/play/${encodeURIComponent(track.videoId)}`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "再生情報を取得できませんでした");

        audio.src = data.url;
        audio.load();
        await audio.play();

        $("playerStatus").textContent = "再生中";
        $("playerSource").textContent = data.source || "Invidious";
        setPlayer(data, false);
    } catch (err) {
        $("playerStatus").textContent = "再生できませんでした";
        $("playerSource").textContent = "";
        showToast(err.message);
    }
}

function setPlayer(track, loading) {
    $("nowTitle").textContent = track.title || "Untitled";
    $("nowArtist").textContent = track.author || "";
    $("nowArt").src = track.thumbnail || `https://i.ytimg.com/vi/${track.videoId}/hqdefault.jpg`;
    $("playBtn").textContent = loading ? "…" : (audio.paused ? "▶" : "Ⅱ");
}

$("playBtn").addEventListener("click", async () => {
    if (!audio.src) return;
    if (audio.paused) {
        try { await audio.play(); } catch {}
    } else {
        audio.pause();
    }
});

$("prevBtn").addEventListener("click", () => {
    if (!queue.length) return;
    currentIndex = Math.max(0, currentIndex - 1);
    playTrack(queue[currentIndex]);
});

$("nextBtn").addEventListener("click", () => {
    if (!queue.length) return;
    currentIndex = Math.min(queue.length - 1, currentIndex + 1);
    playTrack(queue[currentIndex]);
});

$("seek").addEventListener("input", () => {
    if (audio.duration) audio.currentTime = (Number($("seek").value) / 1000) * audio.duration;
});

$("volume").addEventListener("input", () => {
    audio.volume = Number($("volume").value);
});

audio.volume = 0.9;

audio.addEventListener("play", () => {
    $("playBtn").textContent = "Ⅱ";
    $("playerStatus").textContent = "再生中";
});

audio.addEventListener("pause", () => {
    $("playBtn").textContent = "▶";
    if (audio.src) $("playerStatus").textContent = "一時停止";
});

audio.addEventListener("timeupdate", () => {
    if (!audio.duration) return;
    $("seek").value = Math.round((audio.currentTime / audio.duration) * 1000);
    $("currentTime").textContent = formatTime(audio.currentTime);
    $("duration").textContent = formatTime(audio.duration);
});

audio.addEventListener("ended", () => {
    if (currentIndex >= 0 && currentIndex < queue.length - 1) {
        currentIndex++;
        playTrack(queue[currentIndex]);
    }
});

$("searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") search();
});

function renderQueue() {
    $("queue").innerHTML = queue.map((item, i) => `
        <div class="queue-item">
            <img src="${escapeHtml(item.thumbnail)}" alt="">
            <div>
                <strong>${escapeHtml(item.title)}</strong>
                <small>${escapeHtml(item.author || "")}</small>
            </div>
            <button onclick="removeQueue(${i})">×</button>
        </div>
    `).join("") || `<div class="queue-empty">キューは空です</div>`;
}

function removeQueue(index) {
    queue.splice(index, 1);
    if (currentIndex >= queue.length) currentIndex = queue.length - 1;
    renderQueue();
}

function showToast(message) {
    const toast = $("toast");
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(showToast.timer);
    showToast.timer = setTimeout(() => toast.classList.remove("show"), 3500);
}

async function loadStatus() {
    try {
        const res = await fetch("/api/status");
        const data = await res.json();
        $("instanceCount").textContent = `${data.healthy}/${data.count} nodes`;
    } catch {
        $("instanceCount").textContent = "network";
    }
}

loadStatus();
renderQueue();
