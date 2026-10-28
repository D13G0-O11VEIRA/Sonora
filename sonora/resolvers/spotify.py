"""Leitura de links do Spotify (faixa, álbum, playlist) - somente METADADOS.

O Spotify protege o áudio com DRM; o Sonora lê título/artistas/álbum/capa aqui
e baixa o áudio equivalente do YouTube (ver downloader.find_best_match).

Duas estratégias, nesta ordem:
 1. API oficial (opcional, precisa de Client ID/Secret nas Configurações).
    Desde fev/2026 apps em "Development Mode" só leem os itens de playlists
    PRÓPRIAS ou colaborativas, por isso há fallback automático.
 2. Página "embed" pública (sem login): traz faixa, álbum ou playlist
    (até ~100 faixas). Álbum/ano/gênero faltantes são completados depois
    pelo enrich.itunes_enrich.
"""
from __future__ import annotations

import json
import re
import time
from typing import Callable, Iterator

import requests

from ..config import DATA_DIR, Settings
from ..models import Resolved, TrackMeta

UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SpotifyError(Exception):
    pass


# =================================================================== API
_token_cache: dict = {"token": "", "exp": 0.0, "key": ""}


def _token(settings: Settings) -> str:
    key = settings.spotify_client_id + settings.spotify_client_secret
    if _token_cache["token"] and _token_cache["exp"] > time.time() + 30 and _token_cache["key"] == key:
        return _token_cache["token"]
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={"grant_type": "client_credentials"},
        auth=(settings.spotify_client_id.strip(), settings.spotify_client_secret.strip()),
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    _token_cache.update(token=j["access_token"], exp=time.time() + j.get("expires_in", 3600), key=key)
    return _token_cache["token"]


def _api_get(settings: Settings, url_or_path: str, params: dict | None = None) -> dict:
    url = url_or_path if url_or_path.startswith("http") else f"https://api.spotify.com/v1{url_or_path}"
    for _ in range(3):
        r = requests.get(url, params=params, headers={"Authorization": f"Bearer {_token(settings)}"}, timeout=20)
        if r.status_code == 429:
            time.sleep(min(int(r.headers.get("Retry-After", "2")) + 1, 30))
            continue
        r.raise_for_status()
        return r.json()
    raise SpotifyError("Spotify limitou as requisições (429). Tente novamente em instantes.")


def _paged(settings: Settings, first: dict) -> Iterator[dict]:
    page = first
    while page:
        yield from page.get("items") or []
        nxt = page.get("next")
        page = _api_get(settings, nxt) if nxt else None


def _track_from_api(t: dict, album: dict | None, settings: Settings, source: str) -> TrackMeta:
    album = t.get("album") or album or {}
    images = album.get("images") or []
    sep = settings.artist_separator
    return TrackMeta(
        title=t.get("name", ""),
        artists=[a["name"] for a in t.get("artists", []) if a.get("name")],
        album=album.get("name", ""),
        album_artist=sep.join(a["name"] for a in album.get("artists", []) if a.get("name")),
        year=(album.get("release_date") or "")[:4],
        track_no=int(t.get("track_number") or 0),
        track_total=int(album.get("total_tracks") or 0),
        disc_no=int(t.get("disc_number") or 0),
        isrc=(t.get("external_ids") or {}).get("isrc", ""),
        duration=(t.get("duration_ms") or 0) / 1000,
        cover_url=images[0]["url"] if images else "",
        thumb_url=images[-1]["url"] if images else "",
        source_url=(t.get("external_urls") or {}).get("spotify", source),
        spotify_id=t.get("id") or "",
        origin="spotify",
    )


def _via_api(kind: str, sid: str, settings: Settings, source: str) -> Resolved:
    if kind == "track":
        t = _api_get(settings, f"/tracks/{sid}")
        return Resolved([_track_from_api(t, None, settings, source)], "", "single")

    if kind == "album":
        a = _api_get(settings, f"/albums/{sid}")
        tracks = [_track_from_api(t, a, settings, source) for t in _paged(settings, a.get("tracks") or {})]
        return Resolved(tracks, a.get("name", "Álbum"), "album")

    if kind == "playlist":
        try:
            name = _api_get(settings, f"/playlists/{sid}", {"fields": "name"}).get("name", "Playlist")
        except Exception:
            name = "Playlist"
        try:  # endpoint novo (2026) e, se falhar, o antigo
            first = _api_get(settings, f"/playlists/{sid}/items", {"limit": 100})
        except requests.HTTPError:
            first = _api_get(settings, f"/playlists/{sid}/tracks", {"limit": 100})
        tracks = []
        for it in _paged(settings, first):
            t = it.get("item") or it.get("track") or {}
            if t.get("type", "track") == "track" and t.get("name"):
                tracks.append(_track_from_api(t, None, settings, source))
        if not tracks:
            raise SpotifyError("A API não retornou faixas desta playlist.")
        return Resolved(tracks, name, "playlist")

    raise SpotifyError(f"Tipo não suportado: {kind}")


# ================================================================== EMBED
def _walk(obj) -> Iterator[dict]:
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _find_entity(data: dict) -> dict | None:
    cur = data
    for k in ("props", "pageProps", "state", "data", "entity"):
        cur = cur.get(k) if isinstance(cur, dict) else None
    if isinstance(cur, dict):
        return cur
    for d in _walk(data):  # layout mudou? procura por estrutura parecida
        if "trackList" in d or ("artists" in d and "duration" in d and "name" in d):
            return d
    return None


def _clean(s: str) -> str:
    return (s or "").replace("\xa0", " ").strip()


def _split_artists(s: str) -> list[str]:
    return [a.strip() for a in re.split(r",\s*|\s*;\s*", _clean(s)) if a.strip()]


def _best_cover(entity: dict) -> str:
    candidates: list[tuple[int, str]] = []
    for key in ("coverArt", "visualIdentity", "images", "image"):
        block = entity.get(key)
        for d in _walk(block):
            url = d.get("url")
            if isinstance(url, str) and url.startswith("http"):
                w = d.get("width") or d.get("maxWidth") or 0
                candidates.append((int(w) if str(w).isdigit() else 0, url))
    return max(candidates)[1] if candidates else ""


def _via_embed(kind: str, sid: str, source: str, settings: Settings, log: Callable[[str], None]) -> Resolved:
    r = requests.get(
        f"https://open.spotify.com/embed/{kind}/{sid}",
        headers={"User-Agent": UA_BROWSER, "Accept-Language": "en-US,en;q=0.9"},
        timeout=20,
    )
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', r.text, re.S)
    if not m:
        raise SpotifyError("A página embed do Spotify mudou de formato (sem __NEXT_DATA__).")
    data = json.loads(m.group(1))
    ent = _find_entity(data)
    if not ent:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "debug_spotify.json").write_text(json.dumps(data, indent=2)[:400000], encoding="utf-8")
        raise SpotifyError("Não consegui interpretar a página do Spotify (dump salvo em data/debug_spotify.json).")

    cover = _best_cover(ent)
    name = _clean(ent.get("name") or ent.get("title") or "")

    if kind == "track":
        artists = [a.get("name", "") for a in ent.get("artists", []) if isinstance(a, dict)]
        artists = [a for a in artists if a] or _split_artists(ent.get("subtitle", ""))
        iso = ((ent.get("releaseDate") or {}).get("isoString") or "")[:4]
        meta = TrackMeta(
            title=name, artists=artists, year=iso,
            duration=float(ent.get("duration") or 0) / 1000,
            cover_url=cover, thumb_url=cover, source_url=source,
            spotify_id=sid, origin="spotify",
        )
        return Resolved([meta], "", "single")

    items = ent.get("trackList") or []
    if not items:
        raise SpotifyError("Nenhuma faixa encontrada nesse link (playlist privada ou vazia?).")
    if len(items) >= 100:
        log("Aviso: sem credenciais do Spotify o limite é ~100 faixas por playlist.")

    album_artist = _clean(ent.get("subtitle", "")) if kind == "album" else ""
    tracks: list[TrackMeta] = []
    for i, it in enumerate(items, 1):
        title = _clean(it.get("title", ""))
        if not title:
            continue
        uri = it.get("uri", "")
        tid = uri.split(":")[-1] if uri.startswith("spotify:track:") else ""
        t = TrackMeta(
            title=title,
            artists=_split_artists(it.get("subtitle", "")),
            duration=float(it.get("duration") or 0) / 1000,
            spotify_id=tid, origin="spotify",
            source_url=f"https://open.spotify.com/track/{tid}" if tid else source,
        )
        if kind == "album":
            t.album, t.album_artist = name, album_artist
            t.track_no, t.track_total = i, len(items)
            t.cover_url = t.thumb_url = cover
        tracks.append(t)
    return Resolved(tracks, name or ("Álbum" if kind == "album" else "Playlist"), kind)


# ================================================================== API pública
def resolve(kind: str, sid: str, source: str, settings: Settings, log: Callable[[str], None]) -> Resolved:
    if settings.has_spotify_credentials:
        try:
            return _via_api(kind, sid, settings, source)
        except Exception as e:  # 401/403/429/estrutura nova... cai para o embed
            log(f"API do Spotify indisponível ({type(e).__name__}); usando leitura pública.")
    try:
        return _via_embed(kind, sid, source, settings, log)
    except SpotifyError:
        raise
    except Exception as e:
        raise SpotifyError(
            f"Não consegui ler o link do Spotify ({e}). Tente colar o link do YouTube "
            "ou configurar as credenciais do Spotify em Configurações."
        ) from e
