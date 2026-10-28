"""Download de áudio (yt-dlp + ffmpeg) e busca da melhor correspondência no YouTube."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

from .config import Settings
from .matching import pick_best
from .models import TrackMeta
from .resolvers.youtube import ydl_base_opts
from .utils import find_ffmpeg, fmt_eta, fmt_speed


class Cancelled(Exception):
    """Lançada quando o usuário cancela um download."""


def search_candidates(query: str, settings: Settings, n: int = 8) -> list[dict]:
    import yt_dlp

    opts = ydl_base_opts(settings, extract_flat=True)
    with yt_dlp.YoutubeDL(opts) as ydl:
        res = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    out = []
    for e in (res or {}).get("entries") or []:
        if e and e.get("id"):
            e = dict(e)
            e["url"] = f"https://www.youtube.com/watch?v={e['id']}"
            out.append(e)
    return out


def find_best_match(meta: TrackMeta, settings: Settings, log: Callable[[str], None] = lambda s: None) -> dict | None:
    """Procura no YouTube a versão que melhor casa com título/artista/duração."""
    base = f"{meta.main_artist} - {meta.title}".strip(" -")
    queries = [base, f"{base} audio"] if meta.origin == "spotify" else [base]
    found: dict[str, dict] = {}
    for q in queries:
        try:
            for c in search_candidates(q, settings):
                found.setdefault(c["id"], c)
        except Exception as e:
            log(f"Busca falhou ('{q}'): {e}")
    best, score = pick_best(meta, list(found.values()))
    if best:
        log(f"Fonte escolhida: {best.get('title')} [{best.get('channel') or best.get('uploader') or '?'}] (pontos: {score:.0f})")
    return best


def download_audio(
    url: str,
    workdir: Path,
    settings: Settings,
    on_progress: Callable[[float, str, str], None],
    on_stage: Callable[[str], None],
    cancel: threading.Event,
) -> tuple[Path, dict]:
    """Baixa o melhor áudio e converte para MP3. Retorna (arquivo, info do yt-dlp)."""
    import yt_dlp

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg não encontrado. Instale (winget install ffmpeg) ou rode: pip install imageio-ffmpeg"
        )
    workdir.mkdir(parents=True, exist_ok=True)

    def hook(d: dict) -> None:
        if cancel.is_set():
            raise Cancelled()
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            pct = (done / total * 100) if total else 0.0
            on_progress(min(pct, 99.0), fmt_speed(d.get("speed")), fmt_eta(d.get("eta")))
        elif d.get("status") == "finished":
            on_stage("convert")

    opts = ydl_base_opts(
        settings,
        skip_download=False,
        format="bestaudio/best",
        outtmpl=str(workdir / "%(id)s.%(ext)s"),
        ffmpeg_location=ffmpeg,
        progress_hooks=[hook],
        retries=5,
        fragment_retries=5,
        noprogress=True,
        postprocessors=[{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": settings.bitrate,
        }],
    )
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except Exception:
        if cancel.is_set():
            raise Cancelled()
        raise

    path: Path | None = None
    for rd in info.get("requested_downloads") or []:
        if rd.get("filepath") and Path(rd["filepath"]).exists():
            path = Path(rd["filepath"])
    if path is None:
        found = sorted(workdir.glob(f"{info.get('id', '*')}.mp3")) or sorted(workdir.glob("*.mp3"))
        if not found:
            raise RuntimeError("O arquivo MP3 não foi gerado (verifique o ffmpeg).")
        path = found[0]
    return path, info


def update_ytdlp(log: Callable[[str], None]) -> bool:
    """Atualiza o yt-dlp via pip (o YouTube muda com frequência)."""
    cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp[default]"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        log((p.stdout or p.stderr).strip()[-600:])
        return p.returncode == 0
    except Exception as e:
        log(f"Falha ao atualizar yt-dlp: {e}")
        return False
