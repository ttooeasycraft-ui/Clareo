# ClipAI — Gerador de Clipes Virais

Recebe um link de vídeo, detecta os melhores momentos automaticamente, corta em clipes 9:16 com legenda queimada.

---

## Como funciona

```
URL do vídeo
    │
    ▼
yt-dlp          ← baixa o vídeo (YouTube, TikTok, Instagram, etc.)
    │
    ▼
Whisper         ← transcreve a fala em português (roda local, gratuito)
    │
    ▼
Detecção        ← palavras-chave + picos de volume → score por segmento
    │
    ▼
ffmpeg          ← corta os melhores trechos, converte 9:16, queima legenda ASS
    │
    ▼
Download        ← clipes prontos para reels/shorts
```

---

## Estrutura

```
backend/      ← Python/FastAPI (deploy no Railway)
frontend/     ← HTML/CSS/JS puro (deploy no GitHub Pages)
```

---

## Backend

### Pré-requisitos locais

- Python 3.11+
- ffmpeg instalado no sistema (`apt install ffmpeg` / `brew install ffmpeg`)

### Rodar localmente

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API disponível em `http://localhost:8000`

### Endpoints

| Método | Rota                              | Descrição                        |
|--------|-----------------------------------|----------------------------------|
| POST   | `/process`                        | Inicia o processamento           |
| GET    | `/status/{job_id}`                | Consulta o status do job         |
| GET    | `/download/{job_id}/{filename}`   | Baixa um clipe gerado            |

#### POST /process — body

```json
{
  "url": "https://youtube.com/watch?v=...",
  "keywords": ["caramba", "nossa"],
  "min_clip_duration": 20,
  "max_clip_duration": 60,
  "max_clips": 5
}
```

#### GET /status/{job_id} — resposta

```json
{
  "status": "done",
  "progress": 100,
  "message": "3 clipes prontos!",
  "clips": [
    {
      "filename": "clip_01.mp4",
      "label": "Clipe 1",
      "start": 42.5,
      "end": 62.5,
      "reason": "palavras-chave: caramba + pico de volume"
    }
  ]
}
```

Status possíveis: `queued` → `running` → `done` / `error`

### Deploy no Railway

1. Crie uma conta em [railway.app](https://railway.app)
2. Novo projeto → **Deploy from GitHub repo** → selecione este repo
3. Configure o **Root Directory** como `backend`
4. Railway detecta o `Dockerfile` automaticamente
5. Copie a URL gerada (ex: `https://clipai-backend.up.railway.app`)

> ⚠️ O Railway tem um limite de execução no plano gratuito. Para vídeos longos (>30min), considere o plano pago ou aumente o timeout.

---

## Frontend

### Configurar URL do backend

Edite `frontend/script.js`, linha 4:

```js
const BACKEND_URL = "https://SEU-APP.up.railway.app";
```

### Rodar localmente

Abra `frontend/index.html` direto no navegador, ou:

```bash
cd frontend
npx serve .
```

### Deploy no GitHub Pages

1. Faça push deste repositório para o GitHub
2. Vá em **Settings → Pages**
3. Source: **Deploy from a branch** → branch `main`, pasta `/frontend`
4. Aguarde alguns minutos — o site estará em `https://seu-usuario.github.io/repo/`

> Alternativamente, use Netlify ou Vercel com a pasta `frontend/` — sem configuração extra.

---

## Algoritmo de detecção de momentos

Cada segmento da transcrição recebe um score:

```
score = kw_score + vol_score

kw_score  = nº de palavras-chave encontradas no trecho
            (palavrões valem 2×)
vol_score = quanto o volume médio do trecho supera a
            linha de base (40º percentil do áudio)
```

Os segmentos com maior score são selecionados, estendidos para a duração mínima configurada, e os overlaps são removidos.

---

## Palavras-chave padrão

```
caramba, nossa, meu deus, incrível, impossível, uau, wow, que isso,
sério, mentira, absurdo, fantástico, impressionante, surreal,
não acredito, olha isso, cara, demais,
puta, merda, porra, caralho, kkkk, hahaha, rsrs
```

Você pode adicionar palavras extras pelo frontend (campo "Opções avançadas").

---

## Modelos Whisper disponíveis

| Modelo  | VRAM   | Velocidade | Precisão |
|---------|--------|------------|----------|
| tiny    | ~1 GB  | ⚡⚡⚡      | ★☆☆      |
| base    | ~1 GB  | ⚡⚡        | ★★☆      |
| small   | ~2 GB  | ⚡          | ★★★      |
| medium  | ~5 GB  | lento      | ★★★★     |
| large   | ~10 GB | muito lento| ★★★★★    |

O padrão é `small` — bom equilíbrio entre velocidade e precisão para português.
Para alterar, edite `video_processor.py`, linha `model_name="small"`.

---

## Tecnologias

- **Backend:** Python 3.11, FastAPI, Uvicorn
- **Download:** yt-dlp
- **Transcrição:** OpenAI Whisper (open-source, roda local)
- **Análise de áudio:** ffmpeg, pydub
- **Edição de vídeo:** ffmpeg (corte, 9:16, legendas ASS)
- **Frontend:** HTML/CSS/JS puro (sem framework)
