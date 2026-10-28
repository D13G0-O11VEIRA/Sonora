"""Modelos de dados compartilhados entre resolvers, downloader, tagger e GUI."""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    QUEUED = "Na fila"
    RESOLVING = "Buscando fonte"
    DOWNLOADING = "Baixando"
    CONVERTING = "Convertendo"
    TAGGING = "Gravando metadados"
    DONE = "Concluído"
    SKIPPED = "Já existe"
    ERROR = "Erro"
    CANCELLED = "Cancelado"


FINAL_STATUSES = {Status.DONE, Status.SKIPPED, Status.ERROR, Status.CANCELLED}


@dataclass
class TrackMeta:
    title: str = ""
    artists: list[str] = field(default_factory=list)
    album: str = ""
    album_artist: str = ""
    year: str = ""
    track_no: int = 0
    track_total: int = 0
    disc_no: int = 0
    disc_total: int = 0
    genre: str = ""
    isrc: str = ""
    duration: float = 0.0          # segundos
    cover_url: str = ""            # capa "oficial" (Spotify / iTunes)
    thumb_url: str = ""            # miniatura (YouTube) - usada como último recurso
    source_url: str = ""           # link que o usuário colou
    youtube_url: str = ""          # de onde o áudio será baixado
    youtube_id: str = ""
    spotify_id: str = ""
    origin: str = "youtube"        # youtube | spotify | search
    playlist_name: str = ""
    playlist_index: int = 0

    @property
    def main_artist(self) -> str:
        return self.artists[0] if self.artists else ""

    def artist_line(self, sep: str = ", ") -> str:
        return sep.join(self.artists)


@dataclass
class Resolved:
    """Resultado da leitura de um link: uma ou várias faixas."""
    tracks: list[TrackMeta]
    name: str = ""
    kind: str = "single"           # single | album | playlist


class Batch:
    """Agrupa as faixas de uma playlist/álbum (para pasta + arquivo .m3u8)."""

    def __init__(self, name: str, kind: str, folder: Path):
        self.name = name
        self.kind = kind
        self.folder = folder
        self.jobs: list["Job"] = []
        self.remaining = 0
        self.lock = threading.Lock()


_job_ids = itertools.count(1)


class Job:
    def __init__(self, meta: TrackMeta, batch: Batch | None = None):
        self.id = next(_job_ids)
        self.meta = meta
        self.batch = batch
        self.status = Status.QUEUED
        self.progress = 0.0
        self.speed = ""
        self.eta = ""
        self.error = ""
        self.output_path: Path | None = None
        self.cancel_event = threading.Event()

    @property
    def finished(self) -> bool:
        return self.status in FINAL_STATUSES

    @property
    def active(self) -> bool:
        return not self.finished
