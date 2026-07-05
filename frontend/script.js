// ─── Config ────────────────────────────────────────────────────────────────
// IMPORTANT: Replace with your Railway backend URL after deploy.
// Example: "https://clareo-backend.up.railway.app"
const BACKEND_URL = window.BACKEND_URL || "https://clareo-production-c030.up.railway.app";

// ─── State ─────────────────────────────────────────────────────────────────
let currentJobId = null;
let pollInterval = null;

// ─── Public actions ─────────────────────────────────────────────────────────
async function startProcess() {
  const url = document.getElementById("video-url").value.trim();
  if (!url) { shake("input-card"); return; }
  if (!url.startsWith("http")) {
    showError("Link inválido. Use uma URL completa (https://...).");
    return;
  }

  const keywordsRaw = document.getElementById("keywords").value.trim();
  const keywords = keywordsRaw
    ? keywordsRaw.split(",").map(k => k.trim()).filter(Boolean)
    : null;

  const minDuration = parseFloat(document.getElementById("min-duration").value) || 20;
  const maxDuration = parseFloat(document.getElementById("max-duration").value) || 60;
  const maxClips    = parseInt(document.getElementById("max-clips").value) || 5;

  showSection("processing");
  setProc(5, "Conectando ao servidor...");

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
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Erro ${res.status}: ${res.statusText}`);
    }

    const { job_id } = await res.json();
    currentJobId = job_id;
    startPolling();
  } catch (e) {
    showError(
      e.message.includes("fetch")
        ? "Não foi possível conectar ao servidor. Verifique se o backend está ativo."
        : e.message
    );
  }
}

function reset() {
  stopPolling();
  currentJobId = null;
  document.getElementById("video-url").value = "";
  resetProcSteps();
  showSection("hero");
}

function cancelProcess() {
  stopPolling();
  currentJobId = null;
  resetProcSteps();
  showSection("hero");
}

function toggleKeywords() {
  const row = document.getElementById("keywords-row");
  const isHidden = row.style.display === "none";
  row.style.display = isHidden ? "block" : "none";
}

// ─── Polling ────────────────────────────────────────────────────────────────
function startPolling() {
  if (pollInterval) clearInterval(pollInterval);
  pollInterval = setInterval(pollStatus, 2500);
}

function stopPolling() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

let _pollFailures = 0;
const MAX_POLL_FAILURES = 5;

async function pollStatus() {
  if (!currentJobId) return;
  try {
    const res = await fetch(`${BACKEND_URL}/status/${currentJobId}`);
    if (!res.ok) {
      _pollFailures++;
      if (_pollFailures >= MAX_POLL_FAILURES) {
        stopPolling();
        showError(`Erro ao consultar o servidor (HTTP ${res.status}). Tente novamente.`);
      }
      return;
    }
    _pollFailures = 0;
    const data = await res.json();

    const { status, progress = 0, message = "" } = data;

    if (status === "queued") {
      setProc(progress || 3, message || "Na fila de processamento...");
      activateProcStep(null);
    } else if (status === "running") {
      setProc(progress, message);
      activateProcStepByProgress(progress);
    } else if (status === "done") {
      stopPolling();
      setProc(100, "Pronto!");
      setTimeout(() => showResults(data.clips), 600);
    } else if (status === "error") {
      stopPolling();
      showError(data.error || data.message || "Erro durante o processamento.");
    }
  } catch (e) {
    console.warn("Poll hiccup:", e);
  }
}

// ─── Progress step visual ────────────────────────────────────────────────────
const STEP_IDS = ["psi-download", "psi-transcribe", "psi-detect", "psi-cut"];
const STEP_THRESHOLDS = [10, 26, 55, 62]; // progress % where each step becomes active

function activateProcStepByProgress(pct) {
  let activeIdx = -1;
  for (let i = 0; i < STEP_THRESHOLDS.length; i++) {
    if (pct >= STEP_THRESHOLDS[i]) activeIdx = i;
  }
  activateProcStep(activeIdx);
}

function activateProcStep(activeIdx) {
  STEP_IDS.forEach((id, i) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.classList.remove("active", "done");
    if (i < activeIdx) el.classList.add("done");
    else if (i === activeIdx) el.classList.add("active");
  });
}

function resetProcSteps() {
  STEP_IDS.forEach(id => {
    const el = document.getElementById(id);
    if (el) el.classList.remove("active", "done");
  });
}

// ─── Results ────────────────────────────────────────────────────────────────
function showResults(clips) {
  showSection("results");

  const titleEl = document.getElementById("results-title");
  const subEl   = document.getElementById("results-sub");
  const grid    = document.getElementById("clips-grid");

  titleEl.textContent = clips.length === 1 ? "1 clipe pronto ✓" : `${clips.length} clipes prontos ✓`;
  subEl.textContent   = "Formato 9:16 com legenda karaokê queimada — pronto para Reels e Shorts";
  grid.innerHTML      = "";

  clips.forEach((clip, i) => {
    const duration = Math.round(clip.end - clip.start);
    const startFmt = fmtTime(clip.start);
    const endFmt   = fmtTime(clip.end);

    // Build DOM nodes — never innerHTML with backend-provided text (XSS prevention)
    const card = document.createElement("div");
    card.className = "clip-card";
    card.style.animationDelay = `${i * 80}ms`;

    // Preview box
    const preview = document.createElement("div");
    preview.className = "clip-preview";

    const previewIcon = document.createElement("span");
    previewIcon.className = "clip-preview-icon";
    previewIcon.textContent = "▣";

    const numBadge = document.createElement("span");
    numBadge.className = "clip-number";
    numBadge.textContent = `CLIPE ${i + 1}`;

    const sizeBadge = document.createElement("span");
    sizeBadge.className = "clip-badge";
    sizeBadge.textContent = `9:16 · ${duration}s`;

    // Viral score badge (like opus.pro) — derived from clip score or estimated
    const scoreBadge = document.createElement("div");
    scoreBadge.className = "clip-score";
    const scoreLabel = document.createElement("span");
    scoreLabel.className = "clip-score-label";
    scoreLabel.textContent = "SCORE";
    const scoreValue = document.createElement("span");
    scoreValue.className = "clip-score-value";
    // Use backend score if available, else estimate from index (best clips first)
    const rawScore = clip.score != null
      ? Math.min(99, Math.round(50 + clip.score * 49))
      : Math.max(70, 97 - i * 4);
    scoreValue.textContent = rawScore;
    scoreBadge.append(scoreLabel, scoreValue);

    preview.append(previewIcon, numBadge, sizeBadge, scoreBadge);

    // Body
    const body = document.createElement("div");
    body.className = "clip-body";

    const title = document.createElement("div");
    title.className = "clip-title";
    title.textContent = clip.label;           // safe: textContent

    const meta = document.createElement("div");
    meta.className = "clip-meta";
    meta.textContent = `${startFmt} → ${endFmt}`;

    body.append(title, meta);

    if (clip.reason) {
      const reason = document.createElement("span");
      reason.className = "clip-reason";
      reason.textContent = `✦ ${clip.reason}`;  // safe: textContent
      body.appendChild(reason);
    }

    // Validate filename is safe before building download URL
    const safeFilename = (clip.filename || "").replace(/[^a-zA-Z0-9._-]/g, "");
    const safeJobId    = (currentJobId   || "").replace(/[^a-zA-Z0-9-]/g, "");

    const dlLink = document.createElement("a");
    dlLink.className = "btn-download";
    dlLink.href      = `${BACKEND_URL}/download/${safeJobId}/${safeFilename}`;
    dlLink.download  = safeFilename;
    dlLink.target    = "_blank";
    dlLink.rel       = "noopener noreferrer";
    dlLink.textContent = "⬇ Baixar clipe";

    body.appendChild(dlLink);
    card.append(preview, body);
    grid.appendChild(card);
  });
}

// ─── UI helpers ─────────────────────────────────────────────────────────────
function showSection(name) {
  document.getElementById("section-hero").style.display       = name === "hero"       ? "" : "none";
  document.getElementById("section-processing").style.display = name === "processing" ? "" : "none";
  document.getElementById("section-results").style.display    = name === "results"    ? "" : "none";
  document.getElementById("section-error").style.display      = name === "error"      ? "" : "none";
}

function showError(msg) {
  showSection("error");
  document.getElementById("error-msg").textContent = msg;
}

function setProc(pct, msg) {
  document.getElementById("proc-bar").style.width = `${pct}%`;
  document.getElementById("proc-pct").textContent = `${pct}%`;
  document.getElementById("proc-msg").textContent = msg;
}

function fmtTime(sec) {
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  return h > 0
    ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}`
    : `${m}:${String(s).padStart(2,"0")}`;
}

function shake(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.animation = "none";
  void el.offsetHeight;
  el.style.animation = "shake 0.4s ease";
  setTimeout(() => el.style.animation = "", 400);
}

// ─── Init ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  // Focus input on load for faster UX
  document.getElementById("video-url")?.focus();
});

// CSS keyframe injection for shake
const style = document.createElement("style");
style.textContent = `
@keyframes shake {
  0%,100%{transform:translateX(0)}
  20%{transform:translateX(-6px)}
  40%{transform:translateX(6px)}
  60%{transform:translateX(-4px)}
  80%{transform:translateX(4px)}
}`;
document.head.appendChild(style);
