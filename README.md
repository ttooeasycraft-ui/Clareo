# Clareo — AI Video Clip Generator

> Cole o link de qualquer vídeo. Clareo encontra os melhores momentos, corta em 9:16 e queima legenda estilo karaokê — automático.

---

## Como funciona

```
URL do vídeo
    │
    ▼
yt-dlp          ← baixa o vídeo (YouTube, TikTok, Instagram, Twitch, +1000 sites)
    │
    ▼
Whisper         ← transcreve a fala em português, palavra por palavra (open-source, gratuito)
    │
    ▼
Detecção        ← volume peaks + palavras-chave → score por segmento
    │
    ▼
ffmpeg          ← corta os melhores trechos, converte 9:16, queima legenda karaokê (ASS)
    │
    ▼
Download        ← clipes prontos para Reels, Shorts e TikTok
```

---

## Estrutura

```
backend/          ← Python/FastAPI (deploy no Railway)
  main.py         ← API REST + gerenciamento de jobs assíncronos
  video_processor.py ← pipeline completo: download → transcrição → score → corte
  requirements.txt
  Dockerfile
  railway.toml

frontend/         ← HTML/CSS/JS puro (deploy no GitHub Pages)
  index.html      ← UI completa do Clareo
  style.css       ← design system teal/charcoal
  script.js       ← fetch + polling + download
```

---

## Deploy

### Backend → Railway

1. Crie conta em [railway.app](https://railway.app)
2. **New Project → Deploy from GitHub repo** → selecione este repositório
3. Configure o **Root Directory** para `backend`
4. Railway detecta o `Dockerfile` automaticamente
5. Copie a URL pública gerada (ex: `https://clareo-backend.up.railway.app`)

> **RAM mínima:** o modelo Whisper `small` usa ~2 GB. Ajuste o plano no Railway conforme necessário.
> Para economizar, use `WHISPER_MODEL=tiny` (mais rápido, menos preciso).

**Variáveis de ambiente opcionais (Railway dashboard):**

| Variável        | Padrão  | Descrição                              |
|-----------------|---------|----------------------------------------|
| `WHISPER_MODEL` | `small` | Modelo Whisper: tiny, base, small, medium, large |

### Frontend → GitHub Pages

1. Em `frontend/script.js`, substitua `YOUR-RAILWAY-BACKEND` pela URL do Railway:
   ```js
   const BACKEND_URL = "https://clareo-backend.up.railway.app";
   ```
2. Commit + push para o repositório GitHub
3. **Settings → Pages → Source: Deploy from branch** → `main`, pasta `/frontend`
4. Aguarde ~2 min — disponível em `https://seu-usuario.github.io/clareo/`

---

## Rodar localmente

### Backend

```bash
# Pré-requisitos: Python 3.11+, ffmpeg instalado
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API em `http://localhost:8000`

### Frontend

```bash
cd frontend
# Edite script.js: BACKEND_URL = "http://localhost:8000"
# Abra index.html no navegador, ou:
npx serve .
```

---

## API Reference

### `POST /process`

Inicia o processamento de um vídeo.

```json
{
  "url": "https://youtube.com/watch?v=...",
  "keywords": ["incrível", "absurdo"],
  "min_clip_duration": 20,
  "max_clip_duration": 60,
  "max_clips": 5
}
```

**Resposta:**
```json
{ "job_id": "uuid-do-job" }
```

### `GET /status/{job_id}`

Consulta o status do processamento.

```json
{
  "status": "running",
  "progress": 55,
  "message": "Analisando momentos...",
  "clips": []
}
```

Status: `queued` → `running` → `done` / `error`

### `GET /download/{job_id}/{filename}`

Download direto do clipe gerado.

---

## Algoritmo de detecção

```
score = keyword_score + volume_score

keyword_score = soma de matches de palavras-chave no segmento
                (palavrões e exclamações = peso 2×)

volume_score  = (volume_médio_do_segmento - baseline) / 10
                onde baseline = 40º percentil do áudio inteiro
```

Segmentos são selecionados em ordem decrescente de score, estendidos para `min_clip_duration`, sem overlap.

---

## Legendas karaokê

Usa ASS com tags `\kf` (karaoke fill) — cada palavra acende em teal/ciano conforme é falada:

- Texto base: branco
- Palavra atual: sweep teal da esquerda para direita
- Fonte: Arial Black 82pt, outline preto de 5px
- Posição: terço inferior do frame 9:16

---

## Modelos Whisper

| Modelo   | RAM    | Velocidade | Precisão PT |
|----------|--------|------------|-------------|
| `tiny`   | ~1 GB  | ⚡⚡⚡      | ★☆☆         |
| `base`   | ~1 GB  | ⚡⚡        | ★★☆         |
| `small`  | ~2 GB  | ⚡          | ★★★ (padrão)|
| `medium` | ~5 GB  | lento      | ★★★★        |
| `large`  | ~10 GB | muito lento| ★★★★★       |

---

## Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn
- **Download:** yt-dlp
- **Transcrição:** openai-whisper (local, sem custo de API)
- **Análise de áudio:** ffmpeg, pydub, numpy
- **Edição de vídeo:** ffmpeg (9:16 + ASS karaokê)
- **Frontend:** HTML/CSS/JS puro
