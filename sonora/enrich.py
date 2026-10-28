"""Enriquecimento online: álbum/ano/gênero (iTunes Search), letras (LRCLIB) e capas.

Todas as APIs usadas aqui são públicas e não exigem chave.
"""
from __future__ import annotations

import io
import re
import threading
import time
from typing import Callable

import requests

from .models import TrackMeta
from .utils import extract_featured, similarity

UA = "Sonora/1.0 (desktop music downloader)"
_session = requests.Session()
_session.headers.update({"User-Agent": UA})


class _RateLimiter:
    """Garante um intervalo mínimo entre chamadas (a API do iTunes é sensível a rajadas)."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self.interval
        if delay:
            time.sleep(delay)


_itunes_rl = _RateLimiter(2.5)
_itunes_blocked_until = 0.0


# ------------------------------------------------------------------ iTunes
def _hires_artwork(url: str) -> str:
    return re.sub(r"/\d+x\d+(?:bb)?\.(jpg|png)$", r"/1000x1000bb.\1", url or "")


def _strip_release_suffix(album: str) -> str:
    return re.sub(r"\s+-\s+Single$", "", album or "").strip()


def itunes_enrich(
    meta: TrackMeta,
    log: Callable[[str], None] = lambda s: None,
    overwrite: bool = False,
    countries: tuple[str, ...] = ("US", "BR"),
) -> bool:
    """Completa os campos vazios (ou todos, se overwrite) com o melhor resultado."""
    global _itunes_blocked_until
    if not meta.title or time.time() < _itunes_blocked_until:
        return False

    clean_title, _ = extract_featured(meta.title)
    term = f"{meta.main_artist} {clean_title}".strip()

    for country in countries:
        try:
            _itunes_rl.wait()
            r = _session.get(
                "https://itunes.apple.com/search",
                params={"term": term, "media": "music", "entity": "song", "limit": 10, "country": country},
                timeout=12,
            )
            if r.status_code in (403, 429):
                _itunes_blocked_until = time.time() + 90
                log("iTunes Search limitou as consultas; pausando enriquecimento por 90 s.")
                return False
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            log(f"iTunes indisponível: {type(e).__name__}")
            return False

        best, best_score = None, 0.0
        for res in results:
            cand_title, _ = extract_featured(res.get("trackName", ""))
            t_sim = similarity(cand_title, clean_title)
            if meta.artists:
                a_sim = max(similarity(res.get("artistName", ""), a) for a in meta.artists)
            else:
                a_sim = 0.5
            score = 0.6 * t_sim + 0.4 * a_sim
            ms = res.get("trackTimeMillis")
            if meta.duration and ms:
                diff = abs(meta.duration - ms / 1000)
                score += 0.1 if diff <= 4 else (-0.15 if diff > 15 else 0)
            if t_sim >= 0.7 and score > best_score:
                best, best_score = res, score

        if best and best_score >= 0.78:
            _apply_itunes(meta, best, overwrite)
            return True
    return False


def _apply_itunes(meta: TrackMeta, r: dict, overwrite: bool) -> None:
    def put(attr: str, value) -> None:
        if value and (overwrite or not getattr(meta, attr)):
            setattr(meta, attr, value)

    if not meta.artists and r.get("artistName"):
        meta.artists = [r["artistName"]]
    put("album", _strip_release_suffix(r.get("collectionName", "")))
    put("album_artist", r.get("collectionArtistName", ""))
    put("year", (r.get("releaseDate") or "")[:4])
    put("genre", r.get("primaryGenreName", ""))
    put("track_no", int(r.get("trackNumber") or 0))
    put("track_total", int(r.get("trackCount") or 0))
    put("disc_no", int(r.get("discNumber") or 0))
    put("disc_total", int(r.get("discCount") or 0))
    put("cover_url", _hires_artwork(r.get("artworkUrl100", "")))


# ------------------------------------------------------------------ letras
def fetch_lyrics(meta: TrackMeta, log: Callable[[str], None] = lambda s: None) -> tuple[str, str]:
    """Retorna (letra_simples, letra_sincronizada_lrc) do LRCLIB; '' se não achar."""
    if not (meta.title and meta.main_artist):
        return "", ""
    clean_title, _ = extract_featured(meta.title)
    try:
        r = _session.get(
            "https://lrclib.net/api/search",
            params={"track_name": clean_title, "artist_name": meta.main_artist},
            timeout=12,
        )
        r.raise_for_status()
        items = r.json()
    except Exception as e:
        log(f"Letras indisponíveis: {type(e).__name__}")
        return "", ""

    best, best_score = None, 0.0
    for it in items or []:
        if it.get("instrumental"):
            continue
        score = similarity(it.get("trackName", ""), clean_title) + similarity(it.get("artistName", ""), meta.main_artist)
        if meta.duration and it.get("duration"):
            diff = abs(meta.duration - float(it["duration"]))
            if diff > 8:
                continue
            score += 0.4 if diff <= 3 else 0.1
        if it.get("syncedLyrics"):
            score += 0.1
        if score > best_score:
            best, best_score = it, score
    if not best or best_score < 1.5:
        return "", ""
    return (best.get("plainLyrics") or "").strip(), (best.get("syncedLyrics") or "").strip()


# ------------------------------------------------------------------- capas
def fetch_bytes(url: str, timeout: int = 15) -> bytes:
    r = _session.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def make_cover(data: bytes, size: int = 1000) -> bytes:
    """Converte para JPEG quadrado (corta o centro) com no máximo size x size."""
    from PIL import Image

    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    if abs(w / h - 1) > 0.05:
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side))
    if img.width > size:
        img = img.resize((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()
