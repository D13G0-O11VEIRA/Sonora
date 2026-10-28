"""Janela principal do Sonora."""
from __future__ import annotations

import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

from .. import APP_NAME, __version__
from ..config import BITRATES, Settings
from ..manager import Manager
from ..models import Job, Status
from ..utils import reveal_in_file_manager
from .library import LibraryTab
from .settings_tab import SettingsTab
from .widgets import JobRow, ThumbLoader


class App(ctk.CTk):
    EVENTS_PER_TICK = 40     # limita o trabalho por ciclo para a janela nunca "travar"

    def __init__(self, settings: Settings):
        ctk.set_appearance_mode(settings.theme)
        ctk.set_default_color_theme("blue")
        super().__init__()

        self.settings = settings
        self.manager = Manager(settings)
        self._ui_q: queue.Queue = queue.Queue()
        self.rows: dict[int, JobRow] = {}
        self.thumbs = ThumbLoader(self.ui_call)
        self._dirty = False

        self.title(f"{APP_NAME} {__version__} — Baixador de músicas")
        self.geometry("1120x760")
        self.minsize(940, 620)

        self.tabs = ctk.CTkTabview(self, anchor="nw")
        self.tabs.pack(fill="both", expand=True, padx=12, pady=(8, 12))
        self._build_download_tab(self.tabs.add("Baixar"))
        self.library = LibraryTab(self.tabs.add("Biblioteca"), self)
        self.settings_tab = SettingsTab(self.tabs.add("Configurações"), self)
        self._build_log_tab(self.tabs.add("Log"))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        self.after(600, self.library.scan)

    # ------------------------------------------------------------ infra
    def ui_call(self, fn) -> None:
        """Agenda fn para rodar na thread da interface (seguro chamar de qualquer thread)."""
        self._ui_q.put(fn)

    def notice(self, text: str) -> None:
        self.status_msg.configure(text=text)

    def open_output_folder(self) -> None:
        path = self.settings.out_path
        path.mkdir(parents=True, exist_ok=True)
        reveal_in_file_manager(path)

    def refresh_from_settings(self) -> None:
        self.quality.set(self.settings.bitrate)
        self.folder_label.configure(text=self._short_path(self.settings.output_dir))
        self.settings_tab.reload()
        self.library.scan()

    @staticmethod
    def _short_path(p: str, n: int = 60) -> str:
        return p if len(p) <= n else "…" + p[-(n - 1):]

    # ------------------------------------------------------------ aba Baixar
    def _build_download_tab(self, tab) -> None:
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        top = ctk.CTkFrame(tab)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        top.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            top, anchor="w", justify="left",
            text="Cole links do YouTube, YouTube Music ou Spotify (música, álbum ou playlist) — um por linha.\n"
                 "Também funciona digitando  Artista - Música.",
        ).grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        self.input = ctk.CTkTextbox(top, height=88)
        self.input.grid(row=1, column=0, rowspan=2, sticky="ew", padx=(12, 8), pady=(0, 12))
        ctk.CTkButton(top, text="Adicionar à fila", height=40, command=self._add).grid(
            row=1, column=1, sticky="ew", padx=(0, 12), pady=(0, 4))
        ctk.CTkButton(top, text="Colar da área de transferência", height=32, fg_color="gray40",
                      hover_color="gray30", command=self._paste).grid(
            row=2, column=1, sticky="ew", padx=(0, 12), pady=(0, 12))
        self.input.bind("<Control-Return>", lambda _e: (self._add(), "break")[1])

        opts = ctk.CTkFrame(tab)
        opts.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ctk.CTkLabel(opts, text="Qualidade (kbps)").pack(side="left", padx=(12, 8), pady=10)
        self.quality = ctk.CTkSegmentedButton(opts, values=BITRATES, command=self._set_quality)
        self.quality.set(self.settings.bitrate)
        self.quality.pack(side="left", pady=10)
        ctk.CTkLabel(opts, text="Pasta").pack(side="left", padx=(24, 8))
        self.folder_label = ctk.CTkLabel(opts, text=self._short_path(self.settings.output_dir), anchor="w")
        self.folder_label.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(opts, text="Alterar…", width=90, command=self._choose_folder).pack(side="left", padx=4)
        ctk.CTkButton(opts, text="Abrir", width=70, command=self.open_output_folder).pack(side="left", padx=(4, 12))

        self.list = ctk.CTkScrollableFrame(tab)
        self.list.grid(row=2, column=0, sticky="nsew")
        self.empty = ctk.CTkLabel(self.list, text="A fila está vazia.\nCole um link acima para começar.",
                                  text_color=("gray40", "gray60"), font=ctk.CTkFont(size=14))
        self.empty.pack(pady=60)

        bottom = ctk.CTkFrame(tab, fg_color="transparent")
        bottom.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.summary = ctk.CTkLabel(bottom, text="", anchor="w")
        self.summary.pack(side="left", padx=4)
        self.status_msg = ctk.CTkLabel(bottom, text="", anchor="w", text_color=("gray35", "gray65"))
        self.status_msg.pack(side="left", padx=16, fill="x", expand=True)
        ctk.CTkButton(bottom, text="Cancelar tudo", width=120, fg_color="gray40", hover_color="gray30",
                      command=self.manager.cancel_all).pack(side="right", padx=4)
        ctk.CTkButton(bottom, text="Limpar concluídos", width=140, fg_color="gray40", hover_color="gray30",
                      command=self._clear_finished).pack(side="right", padx=4)

    def _build_log_tab(self, tab) -> None:
        self.log_box = ctk.CTkTextbox(tab, font=ctk.CTkFont(family="Consolas", size=12), wrap="word")
        self.log_box.pack(fill="both", expand=True)
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------ ações
    def _add(self) -> None:
        text = self.input.get("1.0", "end").strip()
        if not text:
            self.notice("Cole um link primeiro.")
            return
        n = self.manager.add_links(text)
        if n:
            self.input.delete("1.0", "end")
            self.notice(f"Lendo {n} link(s)…")

    def _paste(self) -> None:
        try:
            clip = self.clipboard_get().strip()
        except tk.TclError:
            clip = ""
        if clip:
            current = self.input.get("1.0", "end").strip()
            self.input.insert("end", ("\n" if current else "") + clip)
        else:
            self.notice("A área de transferência está vazia.")

    def _set_quality(self, value: str) -> None:
        self.settings.bitrate = value
        self.settings.save()
        self.settings_tab.reload()

    def _choose_folder(self) -> None:
        d = filedialog.askdirectory(initialdir=self.settings.output_dir)
        if d:
            self.settings.output_dir = d
            self.settings.save()
            self.refresh_from_settings()

    def _clear_finished(self) -> None:
        for job in self.manager.forget_finished():
            row = self.rows.pop(job.id, None)
            if row:
                row.destroy()
        self._sync_empty()
        self._update_summary()

    def _row_action(self, action: str, job: Job) -> None:
        if action == "cancel":
            self.manager.cancel(job)
        elif action == "retry":
            self.manager.retry(job)
        elif action == "reveal" and job.output_path:
            reveal_in_file_manager(job.output_path)

    # ------------------------------------------------------------ eventos
    def _poll(self) -> None:
        try:
            for _ in range(self.EVENTS_PER_TICK):
                kind, data = self.manager.events.get_nowait()
                try:
                    self._handle(kind, data)
                except Exception as e:   # um erro de UI nunca deve parar o laço
                    self.manager.logger.error("Erro de interface: %s", e)
        except queue.Empty:
            pass
        try:
            while True:
                self._ui_q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as e:
            self.manager.logger.error("Erro de interface: %s", e)
        if self._dirty:
            self._dirty = False
            self._update_summary()
        self.after(100, self._poll)

    def _handle(self, kind: str, data) -> None:
        if kind == "job_added":
            self.empty.pack_forget()
            row = JobRow(self.list, data, self._row_action, self.thumbs)
            row.pack(fill="x", padx=4, pady=3)
            self.rows[data.id] = row
            self._dirty = True
        elif kind == "job_update":
            row = self.rows.get(data.id)
            if row:
                row.refresh()
            self._dirty = True
        elif kind == "log":
            self._append_log(data)
        elif kind == "notice":
            self.notice(data)
            if any(w in data.lower() for w in ("não consegui", "não suportado", "não são suportados")):
                messagebox.showwarning(APP_NAME, data)
        elif kind == "idle":
            self.notice("Tudo pronto! ✔")
            self.library.scan()

    def _append_log(self, line: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", line + "\n")
        if int(self.log_box.index("end-1c").split(".")[0]) > 2000:
            self.log_box.delete("1.0", "200.0")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _sync_empty(self) -> None:
        if not self.rows:
            self.empty.pack(pady=60)

    def _update_summary(self) -> None:
        jobs = list(self.manager.jobs.values())
        done = sum(j.status in (Status.DONE, Status.SKIPPED) for j in jobs)
        err = sum(j.status == Status.ERROR for j in jobs)
        active = sum(j.active for j in jobs)
        parts = [f"{len(jobs)} na lista", f"{done} concluída(s)"]
        if active:
            parts.append(f"{active} em andamento")
        if err:
            parts.append(f"{err} com erro")
        self.summary.configure(text="  •  ".join(parts))

    # ------------------------------------------------------------ fechar
    def _on_close(self) -> None:
        if any(j.active for j in self.manager.jobs.values()):
            if not messagebox.askyesno(APP_NAME, "Há downloads em andamento. Deseja cancelar e sair?"):
                return
        try:
            self.manager.shutdown()
        finally:
            self.destroy()
            os._exit(0)   # encerra threads de download/rede que ainda estejam presas


def run_gui(settings: Settings) -> None:
    App(settings).mainloop()
