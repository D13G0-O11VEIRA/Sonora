"""Reconhece que tipo de link/entrada o usuário colou."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_SPOTIFY = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?|spotify:)"
    r"(track|album|playlist|artist)[/:]([A-Za-z0-9]{22})"
)
_URL_RE = re.compile(r"(https?://[^\s]+|spotify:(?:track|album|playlist|artist):[A-Za-z0-9]{22})")


@dataclass
class Link:
    kind: str   # spotify_track|spotify_album|spotify_playlist|spotify_artist|youtube_video|youtube_playlist|search|unsupported
    id: str
    url: str


def parse_inputs(text: str) -> list[str]:
    """Separa o texto colado em itens: URLs (várias por linha) ou texto de busca."""
    items: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        urls = _URL_RE.findall(line)
        items.extend(urls if urls else [line])
    return items


def classify(raw: str, playlist_if_both: bool = False) -> Link:
    raw = (raw or "").strip().strip("<>\"'")
    m = _SPOTIFY.search(raw)
    if m:
        return Link(f"spotify_{m.group(1)}", m.group(2), raw)

    if not re.match(r"^https?://", raw, re.I):
        # texto livre => busca no YouTube ("Artista - Música")
        return Link("search", raw, raw)

    u = urlparse(raw)
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    q = parse_qs(u.query)
    vid = (q.get("v") or [None])[0]
    lst = (q.get("list") or [None])[0]

    def video_or_playlist(video_id: str | None, list_id: str | None) -> Link | None:
        is_real_list = bool(list_id) and not list_id.startswith("RD")  # RD = "mix" automático
        if video_id and not (is_real_list and playlist_if_both):
            return Link("youtube_video", video_id, f"https://www.youtube.com/watch?v={video_id}")
        if is_real_list:
            return Link("youtube_playlist", list_id, f"https://www.youtube.com/playlist?list={list_id}")
        return None

    if host == "youtu.be":
        first = u.path.strip("/").split("/")[0]
        link = video_or_playlist(first or None, lst)
        if link:
            return link
    elif host.endswith(("youtube.com", "youtube-nocookie.com")):
        parts = [p for p in u.path.split("/") if p]
        if parts and parts[0] in ("shorts", "embed", "live", "v") and len(parts) > 1:
            vid = parts[1]
        if parts and parts[0] == "playlist" and lst:
            return Link("youtube_playlist", lst, f"https://www.youtube.com/playlist?list={lst}")
        link = video_or_playlist(vid, lst)
        if link:
            return link
    return Link("unsupported", "", raw)
