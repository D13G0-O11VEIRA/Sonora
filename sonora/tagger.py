"""Leitura e gravação de tags ID3 (mutagen).

As tags são gravadas em ID3v2.3 (UTF-16), o formato que o Explorador de Arquivos
do Windows lê melhor: título, "Artistas participantes", álbum, ano, nº da faixa,
gênero, capa, letra etc. aparecem direto no painel de Detalhes.
"""
from __future__ import annotations

from pathlib import Path

from mutagen.id3 import (
    APIC, COMM, ID3, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK, TSRC, USLT, WOAS,
    ID3NoHeaderError,
)
from mutagen.mp3 import MP3

_ENC = 1  # UTF-16 com BOM (compatível com ID3v2.3)

_TEXT_FRAMES = {
    "title": ("TIT2", TIT2),
    "artist": ("TPE1", TPE1),
    "album": ("TALB", TALB),
    "album_artist": ("TPE2", TPE2),
    "genre": ("TCON", TCON),
    "isrc": ("TSRC", TSRC),
}


def _num(n, total=0) -> str:
    n = int(n or 0)
    total = int(total or 0)
    if not n:
        return ""
    return f"{n}/{total}" if total else str(n)


def write_tags(path: str | Path, fields: dict, cover: bytes | None | bool = None) -> None:
    """Grava/atualiza tags preservando as que não foram informadas.

    fields: title, artist, album, album_artist, year, track, track_total, disc,
            disc_total, genre, isrc, comment, lyrics, source_url.
            Chave ausente/None = não mexe; string vazia = apaga o campo.
    cover:  bytes (JPEG/PNG) = define a capa; False = remove; None = mantém.
    """
    path = str(path)
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        tags = ID3()

    for key, (fid, cls) in _TEXT_FRAMES.items():
        if fields.get(key) is not None:
            value = str(fields[key]).strip()
            tags.delall(fid)
            if value:
                tags.add(cls(encoding=_ENC, text=[value]))

    if fields.get("year") is not None:
        tags.delall("TDRC")
        year = str(fields["year"]).strip()
        if year:
            tags.add(TDRC(encoding=_ENC, text=[year]))

    if "track" in fields and fields["track"] is not None:
        tags.delall("TRCK")
        v = _num(fields["track"], fields.get("track_total", 0))
        if v:
            tags.add(TRCK(encoding=_ENC, text=[v]))

    if "disc" in fields and fields["disc"] is not None:
        tags.delall("TPOS")
        v = _num(fields["disc"], fields.get("disc_total", 0))
        if v:
            tags.add(TPOS(encoding=_ENC, text=[v]))

    if fields.get("comment") is not None:
        tags.delall("COMM")
        if str(fields["comment"]).strip():
            tags.add(COMM(encoding=_ENC, lang="eng", desc="", text=[str(fields["comment"]).strip()]))

    if fields.get("lyrics") is not None:
        tags.delall("USLT")
        if str(fields["lyrics"]).strip():
            tags.add(USLT(encoding=_ENC, lang="eng", desc="", text=str(fields["lyrics"]).strip()))

    if fields.get("source_url") is not None:
        tags.delall("WOAS")
        if str(fields["source_url"]).strip():
            tags.add(WOAS(url=str(fields["source_url"]).strip()))

    if cover is False:
        tags.delall("APIC")
    elif isinstance(cover, (bytes, bytearray)) and cover:
        tags.delall("APIC")
        mime = "image/png" if bytes(cover[:8]) == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        tags.add(APIC(encoding=_ENC, mime=mime, type=3, desc="Cover", data=bytes(cover)))

    tags.save(path, v2_version=3)


def _text(tags: ID3 | None, frame_id: str, sep: str = "; ") -> str:
    if not tags:
        return ""
    frames = tags.getall(frame_id)
    if not frames:
        return ""
    return sep.join(str(t) for t in frames[0].text if str(t)).strip()


def read_tags(path: str | Path) -> dict:
    """Lê as tags de um MP3 para um dicionário simples (usado pela Biblioteca)."""
    path = str(path)
    out = {
        "path": path, "title": "", "artist": "", "album": "", "album_artist": "",
        "year": "", "track": "", "disc": "", "genre": "", "isrc": "", "comment": "",
        "lyrics": "", "has_cover": False, "duration": 0.0, "bitrate": 0,
    }
    try:
        audio = MP3(path)
        out["duration"] = float(audio.info.length)
        out["bitrate"] = int(audio.info.bitrate // 1000)
        tags = audio.tags
    except Exception:
        return out
    if tags is None:
        return out
    out.update(
        title=_text(tags, "TIT2"), artist=_text(tags, "TPE1"), album=_text(tags, "TALB"),
        album_artist=_text(tags, "TPE2"), genre=_text(tags, "TCON"), isrc=_text(tags, "TSRC"),
        year=_text(tags, "TDRC")[:4], track=_text(tags, "TRCK"), disc=_text(tags, "TPOS"),
    )
    comm = tags.getall("COMM")
    out["comment"] = str(comm[0].text[0]) if comm and comm[0].text else ""
    uslt = tags.getall("USLT")
    out["lyrics"] = uslt[0].text if uslt else ""
    out["has_cover"] = bool(tags.getall("APIC"))
    return out


def read_cover(path: str | Path) -> bytes | None:
    try:
        tags = ID3(str(path))
    except Exception:
        return None
    pics = tags.getall("APIC")
    return pics[0].data if pics else None


def split_track(value: str) -> tuple[int, int]:
    """'3/12' -> (3, 12)"""
    a, _, b = (value or "").partition("/")
    return (int(a) if a.strip().isdigit() else 0, int(b) if b.strip().isdigit() else 0)
