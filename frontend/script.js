// ─── Config ────────────────────────────────────────────────────────────────
// Set BACKEND_URL to your Railway deployment URL.
// While developing locally, set it to http://localhost:8000
const BACKEND_URL = window.BACKEND_URL || "https://YOUR-RAILWAY-APP.up.railway.app";

// ─── State ─────────────────────────────────────────────────────────────────
let currentJobId = null;
let pollInterval = null;

// ─── Main flow ──────────────────────────────────────────────────────────────
async function startProcess() {
  const url = document.getElementById("video-url").value.trim();
  if (!url) {
    alert("Cole o link do vídeo primeiro!");
    return;
  }
  if (!url.startsWith("http")) {
    alert("Link inválido. Use uma URL completa começando com https://");
    return;
  }

  // Parse options
  const keywordsRaw = document.getElementById("keywords").value.trim();
  const keywords = keywordsRaw
    ? keywordsRaw.split(",").map((k) => k.trim()).filter(Boolean)
    : null;
  const minDuration = parseFloat(document.getElementById("min-duration").value) || 20;
  const maxDuration = parseFloat(document.getElementById("max-duration").value) || 60;
  const maxClips = parseInt(document.getElementById("max-clips").value) || 5;

  // UI
  showCard("progress");
  setProgress(5, "Enviando para o servidor...");

  try {
    const res = await fetch(`${BACKEND_URL}/process`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        keywords,
        min_clip_duration: minDuration,
        max_clip_duration: maxDuration,
        max_clips: maxClips,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || "Erro ao iniciar processamento");
    }

    const data = await res.json();
    currentJobId = data.job_id;
    startPolling();
  } catch (e) {
    showError(e.message || "Falha ao conectar ao servidor. Verifique se o backend está rodando.");
  }
}

// ─── Polling ────────────────────────────────────────────────────────────────
function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollStatus, 2000);
}

async function pollStatus() {
  if (!currentJobId) return;

  try {
    const res = await fetch(`${BACKEND_URL}/status/${currentJobId}`);
    if (!res.ok) throw new Error("Falha ao buscar status");
    const data = await res.json();

    switch (data.status) {
      case "queued":
        setProgress(data.progress || 2, data.message || "Na fila...");
        break;
      case "running":
        setProgress(data.progress || 10, data.message || "Processando...");
        break;
      case "done":
        stopPolling();
        showResults(data.clips);
        break;
      case "error":
        stopPolling();
        showError(data.error || data.message || "Erro desconhecido");
        break;
    }
  } catch (e) {
    // Network hiccup — keep trying
    console.warn("Poll error:", e);
  }
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

// ─── Results ────────────────────────────────────────────────────────────────
function showResults(clips) {
  showCard("results");
  const title = document.getElementById("results-title");
  const grid = document.getElementById("clips-grid");

  title.textContent = clips.length === 1 ? "1 clipe pronto!" : `${clips.length} clipes prontos!`;
  grid.innerHTML = "";

  clips.forEach((clip) => {
    const startFmt = formatTime(clip.start);
    const endFmt = formatTime(clip.end);
    const duration = Math.round(clip.end - clip.start);

    const item = document.createElement("div");
    item.className = "clip-item";
    item.innerHTML = `
      <div class="clip-info">
        <div class="clip-label">${clip.label}</div>
        <div class="clip-meta">${startFmt} → ${endFmt} &nbsp;·&nbsp; ${duration}s &nbsp;·&nbsp; 9:16 com legenda</div>
        ${clip.reason ? `<div class="clip-reason">📌 ${clip.reason}</div>` : ""}
      </div>
      <a
        class="btn-download"
        href="${BACKEND_URL}/download/${currentJobId}/${clip.filename}"
        download="${clip.filename}"
        target="_blank"
      >
        ⬇ Baixar
      </a>
    `;
    grid.appendChild(item);
  });
}

// ─── Helpers ────────────────────────────────────────────────────────────────
function setProgress(pct, msg) {
  document.getElementById("progress-bar").style.width = `${pct}%`;
  document.getElementById("progress-pct").textContent = `${pct}%`;
  document.getElementById("progress-msg").textContent = msg;
}

function showCard(name) {
  ["form", "progress", "results", "error"].forEach((id) => {
    const el = document.getElementById(`card-${id}`);
    if (el) el.style.display = id === name ? "" : "none";
  });
}

function showError(msg) {
  showCard("error");
  document.getElementById("error-msg").textContent = msg;
}

function reset() {
  stopPolling();
  currentJobId = null;
  document.getElementById("video-url").value = "";
  showCard("form");
}

function cancelProcess() {
  stopPolling();
  currentJobId = null;
  showCard("form");
}

function formatTime(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

// ─── Show backend config notice if still default URL ─────────────────────
document.addEventListener("DOMContentLoaded", () => {
  if (BACKEND_URL.includes("YOUR-RAILWAY-APP")) {
    const card = document.getElementById("card-form");
    const notice = document.createElement("div");
    notice.className = "config-notice";
    notice.innerHTML = `
      ⚙️ Configure o backend: edite <code>script.js</code> e troque
      <code>YOUR-RAILWAY-APP</code> pela URL do seu deploy no Railway.
    `;
    card.appendChild(notice);
  }
});
