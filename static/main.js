async function search() {
    const query = document.getElementById('searchInput').value.trim();
    if (!query) return;
    const res = await fetch(`/api/search?query=${encodeURIComponent(query)}`);
    const videos = await res.json();
    const container = document.getElementById('results');
    container.innerHTML = '';
    videos.forEach(v => {
        const card = document.createElement('div');
        card.className = 'col-md-4 mb-3';
        card.innerHTML = `
            <div class="card bg-secondary">
                <img src="${v.thumbnail}" class="card-img-top">
                <div class="card-body">
                    <h5>${v.title}</h5>
                    <button onclick="play('${v.videoId}')" class="btn btn-success w-100">再生（高音質）</button>
                </div>
            </div>
        `;
        container.appendChild(card);
    });
}

function play(videoId) {
    if (confirm(`この動画を高音質で再生しますか？`)) {
        fetch(`/api/play/${video_id}`)
            .then(r => r.json())
            .then(data => {
                const container = document.getElementById('results');
                container.innerHTML = data.player;
            });
    }
}
