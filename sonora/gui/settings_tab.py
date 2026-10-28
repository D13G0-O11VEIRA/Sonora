"""Aba de Configurações."""
from __future__ import annotations

import threading
import webbrowser
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..config import BITRATES, BROWSERS, TEMPLATE_PRESETS
from ..downloader import update_ytdlp
from ..utils import find_ffmpeg, render_template

SAMPLE = {
    "artist": "Mother Mother", "artists": "Mother Mother", "title": "Burning Pile",
    "album": "O My Heart", "album_artist": "Mother Mother", "year": "2011", "track": "02",
    "disc": "1", "genre": "Indie Rock", "index": "07", "playlist": "Minhas músicas",
}
THEMES = {"Escuro": "dark", "Claro": "light", "Seguir o sistema": "system"}
COOKIES = {"(nenhum)": "", **{b.capitalize(): b for b in BROWSERS if b}}


class SettingsTab:
    def __init__(self, parent, app):
        self.app = app
        s = app.settings
        self.vars: dict[str, ctk.Variable] = {}
        self.maps: dict[str, dict[str, str]] = {}

        self.frame = ctk.CTkScrollableFrame(parent)
        self.frame.pack(fill="both", expand=True)
        self.frame.grid_columnconfigure(1, weight=1)
        self._row = 0

        self._section("Destino e nomes dos arquivos")
        self._entry("Pasta de destino", "output_dir", browse=True)
        self._choice("Qualidade do MP3 (kbps)", "bitrate", {b: b for b in BITRATES})
        self._entry("Modelo do nome", "filename_template")
        self.preset = ctk.CTkOptionMenu(
            self.frame, values=list(TEMPLATE_PRESETS), command=self._pick_preset, width=260
        )
        self.preset.set("Modelos prontos…")
        self.preset.grid(row=self._row, column=1, sticky="w", padx=10, pady=(0, 2))
        self._row += 1
        self.preview = ctk.CTkLabel(self.frame, text="", anchor="w", text_color=("gray35", "gray65"))
        self.preview.grid(row=self._row, column=1, sticky="ew", padx=12, pady=(0, 6))
        self._row += 1
        self._hint("Variáveis: {artist} {artists} {title} {album} {album_artist} {year} {track} {disc} "
                   "{genre} {index} {playlist}. Use / para criar subpastas.")
        self._check("Playlists e álbuns em uma subpasta própria", "playlist_subfolder")
        self._check("Criar arquivo .m3u8 para playlists/álbuns", "create_m3u")
        self._check("Pular músicas que já foram baixadas", "skip_existing")

        self._section("Metadados")
        self._check("Embutir capa do álbum", "embed_cover")
        self._check("Completar álbum/ano/gênero online (iTunes)", "enrich_online")
        self._check("Buscar e embutir letra da música (LRCLIB)", "fetch_lyrics")
        self._check("Salvar também a letra sincronizada (.lrc)", "save_lrc")
        self._entry("Separador de artistas", "artist_separator")
        self._hint('Use "; " para o Windows listar cada um em "Artistas participantes".')

        self._section("Downloads")
        self._slider("Downloads simultâneos", "concurrent_downloads", 1, 6)
        self._check("Link com vídeo + playlist baixa a playlist inteira", "watch_list_is_playlist")
        self._choice("Cookies do navegador", "cookies_browser", COOKIES)
        self._hint("Só é necessário para vídeos com restrição de idade ou quando o YouTube pede login.")

        self._section("Spotify (opcional)")
        self._hint("Sem isto o Sonora lê links do Spotify pela página pública (até ~100 faixas por playlist). "
                   "Com credenciais ele usa a API oficial quando possível.")
        self._entry("Client ID", "spotify_client_id")
        self._entry("Client Secret", "spotify_client_secret", show="•")
        ctk.CTkButton(
            self.frame, text="Abrir painel de desenvolvedores do Spotify", width=300,
            command=lambda: webbrowser.open("https://developer.spotify.com/dashboard"),
        ).grid(row=self._row, column=1, sticky="w", padx=10, pady=4)
        self._row += 1

        self._section("Aparência e ferramentas")
        self._choice("Tema", "theme", THEMES)
        tools = ctk.CTkFrame(self.frame, fg_color="transparent")
        tools.grid(row=self._row, column=0, columnspan=2, sticky="w", padx=10, pady=6)
        ctk.CTkButton(tools, text="Atualizar yt-dlp", width=150, command=self._update_ytdlp).pack(side="left", padx=(0, 8))
        ctk.CTkButton(tools, text="Verificar ffmpeg", width=150, command=self._check_ffmpeg).pack(side="left")
        self._row += 1

        ctk.CTkButton(parent, text="Salvar configurações", height=38, command=self.save).pack(pady=(8, 0))

        self.vars["filename_template"].trace_add("write", lambda *_: self._update_preview())
        self._update_preview()

    # ---------------------------------------------------------- builders
    def _section(self, title: str) -> None:
        ctk.CTkLabel(self.frame, text=title, font=ctk.CTkFont(size=16, weight="bold"), anchor="w").grid(
            row=self._row, column=0, columnspan=2, sticky="ew", padx=10, pady=(18, 6))
        self._row += 1

    def _hint(self, text: str) -> None:
        ctk.CTkLabel(self.frame, text=text, anchor="w", justify="left", wraplength=640,
                     text_color=("gray35", "gray65"), font=ctk.CTkFont(size=12)).grid(
            row=self._row, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 4))
        self._row += 1

    def _entry(self, label: str, attr: str, browse: bool = False, show: str | None = None) -> None:
        var = ctk.StringVar(value=str(getattr(self.app.settings, attr)))
        self.vars[attr] = var
        ctk.CTkLabel(self.frame, text=label, anchor="w").grid(row=self._row, column=0, sticky="w", padx=10, pady=4)
        holder = ctk.CTkFrame(self.frame, fg_color="transparent")
        holder.grid(row=self._row, column=1, sticky="ew", padx=10, pady=4)
        holder.grid_columnconfigure(0, weight=1)
        ctk.CTkEntry(holder, textvariable=var, show=show).grid(row=0, column=0, sticky="ew")
        if browse:
            ctk.CTkButton(holder, text="Escolher…", width=90, command=lambda: self._browse(var)).grid(
                row=0, column=1, padx=(8, 0))
        self._row += 1

    def _check(self, label: str, attr: str) -> None:
        var = ctk.BooleanVar(value=bool(getattr(self.app.settings, attr)))
        self.vars[attr] = var
        ctk.CTkSwitch(self.frame, text=label, variable=var).grid(
            row=self._row, column=0, columnspan=2, sticky="w", padx=12, pady=4)
        self._row += 1

    def _choice(self, label: str, attr: str, options: dict[str, str]) -> None:
        self.maps[attr] = options
        current = str(getattr(self.app.settings, attr))
        display = next((k for k, v in options.items() if v == current), next(iter(options)))
        var = ctk.StringVar(value=display)
        self.vars[attr] = var
        ctk.CTkLabel(self.frame, text=label, anchor="w").grid(row=self._row, column=0, sticky="w", padx=10, pady=4)
        ctk.CTkOptionMenu(self.frame, values=list(options), variable=var, width=220).grid(
            row=self._row, column=1, sticky="w", padx=10, pady=4)
        self._row += 1

    def _slider(self, label: str, attr: str, lo: int, hi: int) -> None:
        var = ctk.DoubleVar(value=float(getattr(self.app.settings, attr)))
        self.vars[attr] = var
        ctk.CTkLabel(self.frame, text=label, anchor="w").grid(row=self._row, column=0, sticky="w", padx=10, pady=4)
        holder = ctk.CTkFrame(self.frame, fg_color="transparent")
        holder.grid(row=self._row, column=1, sticky="w", padx=10, pady=4)
        value_lbl = ctk.CTkLabel(holder, text=str(int(var.get())), width=30)
        ctk.CTkSlider(holder, from_=lo, to=hi, number_of_steps=hi - lo, variable=var, width=220,
                      command=lambda v: value_lbl.configure(text=str(int(round(v))))).pack(side="left")
        value_lbl.pack(side="left", padx=8)
        self._row += 1

    # ---------------------------------------------------------- actions
    def _browse(self, var: ctk.StringVar) -> None:
        d = filedialog.askdirectory(initialdir=var.get() or None)
        if d:
            var.set(d)

    def _pick_preset(self, name: str) -> None:
        self.vars["filename_template"].set(TEMPLATE_PRESETS[name])
        self.preset.set("Modelos prontos…")

    def _update_preview(self) -> None:
        try:
            rel = render_template(self.vars["filename_template"].get(), SAMPLE)
            self.preview.configure(text=f"Exemplo:  {rel.as_posix()}.mp3")
        except Exception:
            self.preview.configure(text="Modelo inválido")

    def reload(self) -> None:
        """Atualiza os controles quando algo mudou fora desta aba (ex.: pasta/qualidade)."""
        s = self.app.settings
        self.vars["output_dir"].set(s.output_dir)
        for attr, options in self.maps.items():
            cur = str(getattr(s, attr))
            self.vars[attr].set(next((k for k, v in options.items() if v == cur), next(iter(options))))

    def save(self) -> None:
        s = self.app.settings
        for attr, var in self.vars.items():
            raw = var.get()
            if attr in self.maps:
                raw = self.maps[attr].get(raw, raw)
            cur = getattr(s, attr)
            if isinstance(cur, bool):
                setattr(s, attr, bool(raw))
            elif isinstance(cur, int):
                setattr(s, attr, int(round(float(raw))))
            else:
                setattr(s, attr, str(raw))
        s.concurrent_downloads = max(1, min(6, s.concurrent_downloads))
        s.artist_separator = s.artist_separator or "; "
        s.filename_template = s.filename_template.strip() or "{artist} - {title}"
        s.save()
        self.app.manager.apply_settings()
        ctk.set_appearance_mode(s.theme)
        self.app.refresh_from_settings()
        messagebox.showinfo("Sonora", "Configurações salvas.")

    def _update_ytdlp(self) -> None:
        self.app.notice("Atualizando yt-dlp… (pode levar um minuto)")

        def work() -> None:
            ok = update_ytdlp(self.app.manager.log)
            self.app.ui_call(lambda: messagebox.showinfo(
                "Sonora",
                "yt-dlp atualizado! Reinicie o Sonora para usar a nova versão." if ok
                else "Não foi possível atualizar. Veja detalhes na aba Log."))

        threading.Thread(target=work, daemon=True).start()

    def _check_ffmpeg(self) -> None:
        path = find_ffmpeg()
        messagebox.showinfo("ffmpeg", f"Encontrado em:\n{path}" if path else
                            "ffmpeg não encontrado.\nInstale com:  winget install ffmpeg\n"
                            "ou rode:  pip install imageio-ffmpeg")
