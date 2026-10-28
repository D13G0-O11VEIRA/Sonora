"""Configurações persistentes (data/config.json) e caminhos do projeto."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = APP_DIR / "data"
TMP_DIR = DATA_DIR / "tmp"
CONFIG_FILE = DATA_DIR / "config.json"
LOG_FILE = DATA_DIR / "sonora.log"
DB_FILE = DATA_DIR / "history.db"

# Modelos de nome de arquivo. Variáveis: {artist} {artists} {title} {album}
# {album_artist} {year} {track} {disc} {genre} {index} {playlist}
# Use "/" para criar subpastas.
TEMPLATE_PRESETS = {
    "Artista - Título": "{artist} - {title}",
    "Artista / Álbum / Nº - Título": "{artist}/{album}/{track} - {title}",
    "Álbum / Nº - Artista - Título": "{album}/{track} - {artist} - {title}",
    "Somente o título": "{title}",
    "Nº da playlist - Artista - Título": "{index} - {artist} - {title}",
}

BITRATES = ["128", "192", "256", "320"]
BROWSERS = ["", "chrome", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]


@dataclass
class Settings:
    output_dir: str = str(Path.home() / "Music" / "Sonora")
    bitrate: str = "320"
    filename_template: str = "{artist} - {title}"
    playlist_subfolder: bool = True      # playlists/álbuns em uma subpasta própria
    create_m3u: bool = True              # cria .m3u8 para playlists/álbuns
    embed_cover: bool = True
    enrich_online: bool = True           # completa álbum/ano/gênero via iTunes Search
    fetch_lyrics: bool = True            # busca letras no LRCLIB
    save_lrc: bool = False               # salva .lrc (letra sincronizada) ao lado do mp3
    skip_existing: bool = True
    concurrent_downloads: int = 3
    artist_separator: str = "; "         # como o Windows separa "Artistas participantes"
    watch_list_is_playlist: bool = False  # link com vídeo + playlist => baixa a playlist toda
    cookies_browser: str = ""            # útil para vídeos com restrição de idade
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    theme: str = "dark"                  # dark | light | system

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls) -> "Settings":
        s = cls()
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return s
        for f in fields(cls):
            if f.name in raw and isinstance(raw[f.name], type(getattr(s, f.name))):
                setattr(s, f.name, raw[f.name])
        s.concurrent_downloads = max(1, min(6, int(s.concurrent_downloads)))
        if s.bitrate not in BITRATES:
            s.bitrate = "320"
        return s

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @property
    def out_path(self) -> Path:
        return Path(self.output_dir).expanduser()

    @property
    def has_spotify_credentials(self) -> bool:
        return bool(self.spotify_client_id.strip() and self.spotify_client_secret.strip())
