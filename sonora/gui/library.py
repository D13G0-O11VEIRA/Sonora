"""Aba Biblioteca (lista os MP3 da pasta de destino) e editor de tags."""
from __future__ import annotations

import io
import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

from .. import enrich
from ..models import TrackMeta
from ..tagger import read_cover, read_tags, split_track, write_tags
from ..utils import fmt_duration, render_template, reveal_in_file_manager, unique_path


class LibraryTab:
    COLUMNS = (("title", "Título", 280), ("artist", "Artistas participantes", 220),
               ("album", "Álbum", 200), ("year", "Ano", 60), ("dur", "Duração", 70), ("kbps", "kbps", 60))

    def __init__(self, parent, app):
        self.app = app
        self.items: list[dict] = []

        bar = ctk.CTkFrame(parent)
        bar.pack(fill="x", pady=(0, 8))
        self.search = ctk.CTkEntry(bar, placeholder_text="Buscar por título, artista ou álbum…")
        self.search.pack(side="left", fill="x", expand=True, padx=(10, 8), pady=10)
        self.search.bind("<KeyRelease>", lambda _e: self._populate())
        ctk.CTkButton(bar, text="Atualizar", width=90, command=self.scan).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Editar tags", width=100, command=self.edit_selected).pack(side="left", padx=4)
        ctk.CTkButton(bar, text="Abrir pasta", width=100, command=self.app.open_output_folder).pack(side="left", padx=(4, 10))

        holder = ctk.CTkFrame(parent)
        holder.pack(fill="both", expand=True)
        self._style()
        self.tree = ttk.Treeview(holder, columns=[c[0] for c in self.COLUMNS], show="headings", selectmode="browse")
        for key, label, width in self.COLUMNS:
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w" if key in ("title", "artist", "album") else "center")
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        sb.pack(side="right", fill="y", pady=8, padx=(0, 8))
        self.tree.bind("<Double-1>", lambda _e: self.edit_selected())

        self.menu = tk.Menu(self.tree, tearoff=0)
        self.menu.add_command(label="Editar tags…", command=self.edit_selected)
        self.menu.add_command(label="Mostrar na pasta", command=self._reveal)
        self.tree.bind("<Button-3>", self._popup)
        self.tree.bind("<Button-2>", self._popup)

        self.count = ctk.CTkLabel(parent, text="", anchor="w")
        self.count.pack(fill="x", padx=10, pady=(6, 0))

    @staticmethod
    def _style() -> None:
        style = ttk.Style()
        style.theme_use("default")
        dark = ctk.get_appearance_mode() == "Dark"
        bg, fg = ("#1e1e1e", "#e6e6e6") if dark else ("#fafafa", "#1a1a1a")
        style.configure("Treeview", background=bg, foreground=fg, fieldbackground=bg, rowheight=26, borderwidth=0)
        style.map("Treeview", background=[("selected", "#1f6aa5")], foreground=[("selected", "white")])
        style.configure("Treeview.Heading", background="#2b2b2b" if dark else "#e5e5e5",
                        foreground=fg, relief="flat", font=("Segoe UI", 10, "bold"))
        style.map("Treeview.Heading", background=[("active", "#3a3a3a" if dark else "#d5d5d5")])

    # ------------------------------------------------------------- dados
    def scan(self) -> None:
        folder = self.app.settings.out_path
        self.count.configure(text="Lendo biblioteca…")

        def work() -> None:
            rows = []
            if folder.exists():
                for p in sorted(folder.rglob("*.mp3")):
                    rows.append(read_tags(p))
            self.app.ui_call(lambda: self._loaded(rows))

        threading.Thread(target=work, daemon=True).start()

    def _loaded(self, rows: list[dict]) -> None:
        self.items = rows
        self._populate()

    def _populate(self) -> None:
        q = self.search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for i, d in enumerate(self.items):
            hay = f"{d['title']} {d['artist']} {d['album']} {Path(d['path']).name}".lower()
            if q and q not in hay:
                continue
            title = d["title"] or Path(d["path"]).stem
            self.tree.insert("", "end", iid=str(i), values=(
                title, d["artist"], d["album"], d["year"], fmt_duration(d["duration"]), d["bitrate"] or ""))
            shown += 1
        self.count.configure(text=f"{shown} de {len(self.items)} músicas  •  {self.app.settings.out_path}")

    # ------------------------------------------------------------- ações
    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        return self.items[int(sel[0])] if sel else None

    def _popup(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    def _reveal(self) -> None:
        d = self._selected()
        if d:
            reveal_in_file_manager(Path(d["path"]))

    def edit_selected(self) -> None:
        d = self._selected()
        if not d:
            messagebox.showinfo("Sonora", "Selecione uma música na lista.")
            return
        TagEditor(self.app, Path(d["path"]), on_saved=self.scan)


class TagEditor(ctk.CTkToplevel):
    FIELDS = (
        ("title", "Título"), ("artist", "Artistas participantes (separe com ;)"), ("album", "Álbum"),
        ("album_artist", "Artista do álbum"), ("year", "Ano"), ("track", "Faixa (ex.: 3/12)"),
        ("disc", "Disco (ex.: 1/1)"), ("genre", "Gênero"), ("isrc", "ISRC"), ("comment", "Comentário"),
    )

    def __init__(self, app, path: Path, on_saved):
        super().__init__(app)
        self.app, self.path, self.on_saved = app, path, on_saved
        self.title(f"Editar tags — {path.name}")
        self.geometry("820x700")
        self.minsize(760, 640)
        self.transient(app)
        self._cover_state: bytes | None | bool = None   # None=manter, False=remover, bytes=trocar
        self._cover_img = None

        data = read_tags(path)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self, width=240)
        left.grid(row=0, column=0, sticky="ns", padx=(14, 8), pady=14)
        self.cover_label = ctk.CTkLabel(left, text="Sem capa", width=220, height=220,
                                        fg_color=("gray82", "gray25"), corner_radius=10)
        self.cover_label.pack(padx=10, pady=(10, 8))
        ctk.CTkButton(left, text="Trocar capa…", command=self._pick_cover).pack(fill="x", padx=10, pady=3)
        ctk.CTkButton(left, text="Remover capa", fg_color="gray40", hover_color="gray30",
                      command=self._remove_cover).pack(fill="x", padx=10, pady=3)
        ctk.CTkButton(left, text="Buscar dados online", command=self._lookup).pack(fill="x", padx=10, pady=(14, 3))
        ctk.CTkButton(left, text="Buscar letra", command=self._lookup_lyrics).pack(fill="x", padx=10, pady=3)
        self.rename = ctk.CTkSwitch(left, text="Renomear arquivo\npelo modelo")
        self.rename.pack(padx=10, pady=14, anchor="w")
        self.info = ctk.CTkLabel(left, text=f"{data['bitrate']} kbps • {fmt_duration(data['duration'])}",
                                 text_color=("gray35", "gray65"))
        self.info.pack(pady=(0, 10))

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 14), pady=14)
        right.columnconfigure(1, weight=1)
        self.entries: dict[str, ctk.CTkEntry] = {}
        for r, (key, label) in enumerate(self.FIELDS):
            ctk.CTkLabel(right, text=label, anchor="w").grid(row=r, column=0, sticky="w", pady=4, padx=(0, 10))
            e = ctk.CTkEntry(right)
            e.grid(row=r, column=1, sticky="ew", pady=4)
            e.insert(0, data.get(key, "") or "")
            self.entries[key] = e
        n = len(self.FIELDS)
        ctk.CTkLabel(right, text="Letra", anchor="w").grid(row=n, column=0, sticky="nw", pady=(8, 0))
        self.lyrics = ctk.CTkTextbox(right, height=180)
        self.lyrics.grid(row=n, column=1, sticky="nsew", pady=(8, 0))
        self.lyrics.insert("1.0", data.get("lyrics", "") or "")
        right.rowconfigure(n, weight=1)

        btns = ctk.CTkFrame(right, fg_color="transparent")
        btns.grid(row=n + 1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ctk.CTkButton(btns, text="Cancelar", width=100, fg_color="gray40", hover_color="gray30",
                      command=self.destroy).pack(side="left", padx=6)
        ctk.CTkButton(btns, text="Salvar", width=120, command=self._save).pack(side="left")

        cover = read_cover(path)
        if cover:
            self._show_cover(cover)
        self.after(150, self._focus)

    def _focus(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self.grab_set()
        except Exception:
            pass

    # ------------------------------------------------------------- capa
    def _show_cover(self, data: bytes | None) -> None:
        if not data:
            self._cover_img = None
            self.cover_label.configure(image=None, text="Sem capa")
            return
        img = Image.open(io.BytesIO(data)).convert("RGB")
        self._cover_img = ctk.CTkImage(light_image=img, dark_image=img, size=(220, 220))
        self.cover_label.configure(image=self._cover_img, text="")

    def _pick_cover(self) -> None:
        f = filedialog.askopenfilename(parent=self, filetypes=[("Imagens", "*.jpg *.jpeg *.png *.webp")])
        if not f:
            return
        try:
            data = enrich.make_cover(Path(f).read_bytes())
        except Exception as e:
            messagebox.showerror("Sonora", f"Não consegui abrir a imagem: {e}", parent=self)
            return
        self._cover_state = data
        self._show_cover(data)

    def _remove_cover(self) -> None:
        self._cover_state = False
        self._show_cover(None)

    # ------------------------------------------------------------- online
    def _current_meta(self) -> TrackMeta:
        artists = [a.strip() for a in self.entries["artist"].get().split(";") if a.strip()]
        return TrackMeta(title=self.entries["title"].get().strip(), artists=artists, origin="search")

    def _lookup(self) -> None:
        meta = self._current_meta()
        if not meta.title:
            messagebox.showinfo("Sonora", "Preencha pelo menos o título.", parent=self)
            return

        def work() -> None:
            found = enrich.itunes_enrich(meta, self.app.manager.log, overwrite=True)
            cover = None
            if found and meta.cover_url:
                try:
                    cover = enrich.make_cover(enrich.fetch_bytes(meta.cover_url))
                except Exception:
                    cover = None
            self.app.ui_call(lambda: self._apply_lookup(found, meta, cover))

        threading.Thread(target=work, daemon=True).start()

    def _apply_lookup(self, found: bool, meta: TrackMeta, cover: bytes | None) -> None:
        if not found:
            messagebox.showinfo("Sonora", "Nenhum resultado confiável encontrado.", parent=self)
            return
        track = f"{meta.track_no}/{meta.track_total}" if meta.track_no and meta.track_total else str(meta.track_no or "")
        disc = f"{meta.disc_no}/{meta.disc_total}" if meta.disc_no and meta.disc_total else str(meta.disc_no or "")
        for key, value in (("album", meta.album), ("album_artist", meta.album_artist), ("year", meta.year),
                           ("track", track), ("disc", disc), ("genre", meta.genre)):
            if value:
                self.entries[key].delete(0, "end")
                self.entries[key].insert(0, value)
        if cover:
            self._cover_state = cover
            self._show_cover(cover)

    def _lookup_lyrics(self) -> None:
        meta = self._current_meta()

        def work() -> None:
            plain, _ = enrich.fetch_lyrics(meta, self.app.manager.log)
            self.app.ui_call(lambda: self._apply_lyrics(plain))

        threading.Thread(target=work, daemon=True).start()

    def _apply_lyrics(self, plain: str) -> None:
        if not plain:
            messagebox.showinfo("Sonora", "Letra não encontrada.", parent=self)
            return
        self.lyrics.delete("1.0", "end")
        self.lyrics.insert("1.0", plain)

    # ------------------------------------------------------------- salvar
    def _save(self) -> None:
        s = self.app.settings
        f = {k: e.get().strip() for k, e in self.entries.items()}
        artists = [a.strip() for a in f["artist"].split(";") if a.strip()]
        f["artist"] = s.artist_separator.join(artists)
        f["track"], f["track_total"] = split_track(f["track"])
        f["disc"], f["disc_total"] = split_track(f["disc"])
        f["lyrics"] = self.lyrics.get("1.0", "end").strip()
        try:
            write_tags(self.path, f, self._cover_state)
            if self.rename.get():
                values = {
                    "artist": artists[0] if artists else "", "artists": ", ".join(artists),
                    "title": f["title"], "album": f["album"], "album_artist": f["album_artist"],
                    "year": f["year"], "track": f"{f['track']:02d}" if f["track"] else "",
                    "disc": str(f["disc"] or ""), "genre": f["genre"], "index": "", "playlist": "",
                }
                name = render_template(s.filename_template, values).name + ".mp3"
                target = self.path.with_name(name)
                if target != self.path:
                    os.replace(self.path, unique_path(target))
        except Exception as e:
            messagebox.showerror("Sonora", f"Não foi possível salvar: {e}", parent=self)
            return
        self.on_saved()
        self.destroy()
