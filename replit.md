# ClipAI — Gerador de Clipes Virais

Recebe um link de vídeo, detecta os melhores momentos automaticamente (picos de volume + palavras-chave), e gera clipes em 9:16 com legenda queimada — prontos para Reels/Shorts.

## Run & Operate

- Backend Python: `cd backend && uvicorn main:app --reload --port 8000`
- Frontend: abra `frontend/index.html` no navegador ou `cd frontend && npx serve .`
- Deploy backend: Railway via `backend/Dockerfile`
- Deploy frontend: GitHub Pages apontando para `frontend/`

## Stack

- **Backend:** Python 3.11, FastAPI, Uvicorn
- **Download:** yt-dlp (YouTube, TikTok, Instagram, etc.)
- **Transcrição:** openai-whisper (roda local, sem API key, em português)
- **Análise de áudio:** ffmpeg, pydub
- **Edição de vídeo:** ffmpeg (corte, padding 9:16, legendas ASS queimadas)
- **Frontend:** HTML/CSS/JS puro (sem framework, vai para GitHub Pages)

## Where things live

- `backend/main.py` — FastAPI app, endpoints REST, gerenciamento de jobs em memória
- `backend/video_processor.py` — pipeline completo: download → transcrição → scoring → corte
- `backend/Dockerfile` — imagem para deploy no Railway
- `backend/railway.toml` — configuração do Railway
- `frontend/index.html` — UI principal
- `frontend/script.js` — lógica de fetch, polling e download (configure `BACKEND_URL` aqui)
- `frontend/style.css` — design dark mode

## Architecture decisions

- Jobs armazenados em memória (dict) + arquivos em `backend/jobs/{job_id}/` — simples e sem dependência de banco. Em caso de restart do servidor, jobs anteriores são perdidos.
- Whisper roda localmente dentro do container Railway — sem custo de API, mas requer RAM (modelo `small` usa ~2 GB).
- Subtítulos em formato ASS para suportar quebra de linha customizada e estilo bold/uppercase.
- CORS aberto (`allow_origins=["*"]`) para compatibilidade com GitHub Pages.
- Processamento assíncrono: POST retorna `job_id` imediatamente, frontend faz polling a cada 2s.

## User preferences

_Populate as you build._

## Gotchas

- Sempre configurar `BACKEND_URL` em `frontend/script.js` antes de fazer deploy no GitHub Pages.
- Railway precisa de pelo menos 2 GB de RAM para o modelo Whisper `small`. Plano gratuito pode não ser suficiente para vídeos longos.
- ffmpeg deve estar instalado no sistema — o Dockerfile já inclui isso.
- yt-dlp precisa ser atualizado periodicamente (`pip install -U yt-dlp`) pois sites mudam seus formatos.
