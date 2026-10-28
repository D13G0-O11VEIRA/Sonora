"""Escolhe, entre os resultados do YouTube, o que melhor corresponde à faixa.

Usado para links do Spotify (o Spotify é protegido por DRM, então o áudio vem do
YouTube e os metadados vêm do Spotify) e para buscas por texto.
"""
from __future__ import annotations

from .models import TrackMeta
from .utils import norm, similarity

# Palavras que indicam outra versão da música (penalizadas, a menos que
# também estejam no título original).
_BAD_WORDS = (
    "live", "ao vivo", "cover", "karaoke", "reaction", "slowed", "sped up",
    "speed up", "nightcore", "8d", "remix", "instrumental", "acoustic",
    "tutorial", "lesson", "mashup", "reverb", "bass boosted", "1 hour", "loop",
)


def score_candidate(meta: TrackMeta, cand: dict) -> float:
    title = cand.get("title") or ""
    chan = cand.get("channel") or cand.get("uploader") or ""
    want = f"{meta.main_artist} {meta.title}".strip()
    nt, nchan, nmeta_title = norm(title), norm(chan), norm(meta.title)

    score = max(similarity(title, want), similarity(title, meta.title)) * 35
    if nmeta_title and nmeta_title in nt:
        score += 10
    if meta.main_artist and (norm(meta.main_artist) in nt or norm(meta.main_artist) in nchan):
        score += 10

    # duração (muito confiável quando conhecida, ex.: vinda do Spotify)
    dur = cand.get("duration")
    if meta.duration and dur:
        diff = abs(meta.duration - dur)
        if diff <= 2:
            score += 40
        elif diff <= 5:
            score += 30
        elif diff <= 10:
            score += 15
        elif diff > 20:
            score -= 40

    low_chan = chan.lower()
    low_title = title.lower()
    if low_chan.endswith("- topic"):
        score += 25            # faixa "auto-gerada" = áudio de estúdio
    if "official audio" in low_title or "audio oficial" in low_title:
        score += 12
    if "official" in low_chan or "vevo" in low_chan:
        score += 6
    if "lyric" in low_title:
        score += 2

    for w in _BAD_WORDS:
        if f" {w} " in f" {nt} " and f" {w} " not in f" {nmeta_title} ":
            score -= 30
    return score


def pick_best(meta: TrackMeta, candidates: list[dict]) -> tuple[dict | None, float]:
    best, best_score = None, float("-inf")
    for c in candidates:
        s = score_candidate(meta, c)
        if s > best_score:
            best, best_score = c, s
    if best is None or best_score < 12:
        return None, best_score
    return best, best_score
