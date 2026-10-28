"""Leitura de vídeos e playlists do YouTube / YouTube Music via yt-dlp."""
from __future__ import annotations

import re
from typing import Callable

from ..config import Settings
from ..models import Resolved, TrackMeta
from ..utils import dedupe, guess_artist_title


def ydl_base_opts(settings: Settings, **extra) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }
    if settings.cookies_browser:
        opts["cookiesfrombrowser"] = (settings.cookies_browser,)
    opts.update(extra)
    return opts


def meta_from_info(info: dict, base: TrackMeta | None = None) -> TrackMeta:
    """Monta/atualiza os metadados a partir do dicionário completo do yt-dlp."""
    m = base or TrackMeta()

    artists = list(info.get("artists") or [])
    if not artists and info.get("artist"):
        artists = [a.strip() for a in re.split(r",\s*", info["artist"]) if a.strip()]
    title = info.get("track") or ""

    if not (title and artists):  # vídeo comum: deduz pelo título/canal
        g_artist, g_title, feats = guess_artist_title(
            info.get("title", ""), info.get("channel") or info.get("uploader") or ""
        )
        title = title or g_title
        if not artists:
            artists = ([g_artist] if g_artist else []) + feats

    m.title = title or m.title
    m.artists = dedupe(artists) or m.artists
    m.album = m.album or info.get("album") or ""
    if not m.year:
        m.year = str(info.get("release_year") or (info.get("release_date") or "")[:4] or "")
    m.genre = m.genre or info.get("genre") or ""
    m.track_no = m.track_no or int(info.get("track_number") or 0)
    m.duration = m.duration or float(info.get("duration") or 0)
    m.youtube_id = info.get("id") or m.youtube_id
    m.youtube_url = info.get("webpage_url") or m.youtube_url
    m.source_url = m.source_url or m.youtube_url
    m.thumb_url = info.get("thumbnail") or m.thumb_url
    return m


def resolve_video(url: str, settings: Settings) -> Resolved:
    import yt_dlp

    with yt_dlp.YoutubeDL(ydl_base_opts(settings)) as ydl:
        info = ydl.extract_info(url, download=False)
    meta = meta_from_info(info, TrackMeta(origin="youtube", source_url=url))
    return Resolved([meta], "", "single")


def resolve_playlist(url: str, settings: Settings, log: Callable[[str], None]) -> Resolved:
    import yt_dlp

    opts = ydl_base_opts(settings, extract_flat="in_playlist", noplaylist=False)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    name = info.get("title") or "Playlist"
    tracks: list[TrackMeta] = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        title = e.get("title") or ""
        if title in ("[Private video]", "[Deleted video]"):
            log(f"Ignorado (indisponível): {e['id']}")
            continue
        vid = e["id"]
        artist, song, feats = guess_artist_title(title, e.get("channel") or e.get("uploader") or "")
        tracks.append(TrackMeta(
            title=song or title,
            artists=dedupe(([artist] if artist else []) + feats),
            duration=float(e.get("duration") or 0),
            origin="youtube",
            youtube_id=vid,
            youtube_url=f"https://www.youtube.com/watch?v={vid}",
            thumb_url=f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
            source_url=url,
        ))
    return Resolved(tracks, name, "playlist")
