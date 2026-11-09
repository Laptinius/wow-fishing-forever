"""Fish tab: Start/Stop, what the bot did and will do next, the frame it matched, stats, and
Scores | Logs sub-tabs (the clip scores of the current cast, or the event log)."""

from __future__ import annotations

from typing import Any

from wowfish.gui import image
from wowfish.gui.presenter import Presenter, View
from wowfish.gui.score_graph import ScoreGraph


class FishPage:
    def __init__(self, parent: Any, presenter: Presenter) -> None:
        from tkinter import BooleanVar
        from tkinter.scrolledtext import ScrolledText

        import ttkbootstrap as tb

        self.p = presenter
        self.frame = tb.Frame(parent, padding=10)
        f = self.frame
        top = tb.Frame(f)
        top.pack(fill="x")
        self.start = tb.Button(
            top, text="Запустить", command=self._start, bootstyle="success", width=12
        )
        self.start.pack(side="left")
        self.stop = tb.Button(
            top,
            text="Остановить",
            command=self.p.stop,
            bootstyle="danger",
            width=12,
            state="disabled",
        )
        self.stop.pack(side="left", padx=6)
        self.state = tb.Label(
            top,
            text="Готово к запуску",
            font=("TkDefaultFont", 12, "bold"),
            bootstyle="secondary",
            padding=(8, 2),
        )
        self.state.pack(side="left", padx=12)
        self.blockers = tb.Label(top, text="", bootstyle="warning")
        self.blockers.pack(side="left", padx=6)

        settings = tb.Frame(f)
        settings.pack(fill="x", pady=(8, 0))
        tb.Label(settings, text="Новый заброс, если полоски нет").pack(side="left")
        self.timeout = tb.Entry(settings, width=5)
        self.timeout.insert(0, f"{presenter.cfg.run.recast_delay_s:g}")
        self.timeout.pack(side="left", padx=6)
        tb.Label(settings, text="сек.").pack(side="left")
        self.movement_enabled = BooleanVar(value=presenter.cfg.run.movement_pause_enabled)
        tb.Checkbutton(settings, text="Пауза WASD — 2 сек.", variable=self.movement_enabled).pack(
            side="left", padx=12
        )
        tb.Button(settings, text="Сохранить", command=self._save_settings).pack(side="left")
        self.settings_note = tb.Label(settings, text="")
        self.settings_note.pack(side="left", padx=6)

        self.did = tb.Label(
            f, text="", font=("TkDefaultFont", 13, "bold"), wraplength=860, justify="left"
        )
        self.did.pack(anchor="w", pady=(10, 0))
        self.next = tb.Label(f, text="", font=("TkDefaultFont", 11), wraplength=860, justify="left")
        self.next.pack(anchor="w", pady=(2, 0))
        self.stats = tb.Label(f, text=presenter.stats)
        self.stats.pack(anchor="w", pady=(2, 8))
        self.cast_bar = tb.Progressbar(f, maximum=100, bootstyle="success")
        self.progress_text = tb.Label(f, text="Готово к запуску", bootstyle="secondary")
        self.progress_text.pack(anchor="w", pady=(0, 3))
        self.cast_bar.pack(fill="x", pady=(0, 8))

        mid = tb.Frame(f)
        mid.pack(fill="both", expand=True)
        self.image = tb.Label(
            mid, text="Здесь появится изображение из WoW", anchor="center", width=60
        )
        self.image.pack(side="left", anchor="n")
        self._photo = None
        right = tb.Frame(mid)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        tabs = tb.Notebook(right)
        tabs.pack(fill="both", expand=True)
        self.graph = ScoreGraph(tabs)
        self.log = ScrolledText(tabs, height=18, wrap="word")
        self.log.configure(state="disabled")
        tabs.add(self.graph.canvas, text="Scores")  # first, so it is the tab shown at start
        tabs.add(self.log, text="Logs")

    def _start(self) -> None:
        reasons = self.p.prerequisites()
        if reasons:
            self.blockers.configure(text="; ".join(reasons))
            return
        self.blockers.configure(text="")
        self.p.start()

    def _save_settings(self) -> None:
        try:
            self.p.save_recast_delay(self.timeout.get())
            self.p.save_movement_pause(self.movement_enabled.get())
        except ValueError as error:
            self.settings_note.configure(text=str(error))
            return
        self.settings_note.configure(text="После следующего запуска")

    def render(self, view: View) -> None:
        labels = {
            "IDLE": ("Работает — подготовка", "success"),
            "CHECKING": ("Работает — проверяет полоску", "info"),
            "CASTING": ("Работает — заброс", "success"),
            "WAIT_BITE": ("Работает — ждёт поклёвку", "success"),
            "INTERACT": ("Работает — собирает рыбу", "success"),
            "PAUSED": ("Пауза — WoW неактивна", "warning"),
            "STOPPED": ("Остановлено", "secondary"),
        }
        status, style = labels.get(view.state, (view.state, "secondary"))
        if view.state == "PAUSED" and view.did.startswith("Paused: WASD:"):
            status = "Пауза — движение WASD"
        if not view.running and view.state == "IDLE":
            status = "Готово к запуску"
        if view.did == "Stopped (session limit)":
            status = "Остановлено — лимит времени"
        if view.did.startswith("Error:") or view.did == "Stopped (error)":
            status, style = "Ошибка — см. журнал", "danger"
        self.state.configure(text=status, bootstyle=style)
        self.did.configure(text=f"Последнее действие — {view.did}")
        self.next.configure(text=f"Далее — {view.next}")
        self.stats.configure(text=view.stats)
        colors = {
            "fishing": "success",
            "arming": "info",
            "movement": "warning",
            "recast": "info",
            "hook": "primary",
            "unfocused": "secondary",
            "stopped": "secondary",
            "error": "danger",
        }
        color = colors.get(view.progress.kind, "secondary")
        self.progress_text.configure(text=view.progress.text, bootstyle=color)
        self.cast_bar.configure(value=100 * view.progress.fraction, bootstyle=color)
        self.start.configure(state="disabled" if view.running else "normal")
        self.stop.configure(state="normal" if view.running else "disabled")
        if view.thumbnail_png is not None and view.thumbnail_png is not getattr(
            self, "_last_png", None
        ):
            self._last_png = view.thumbnail_png
            self._photo = image.photo_from_png(view.thumbnail_png)
            self.image.configure(image=self._photo, text="")
        self.graph.show(view.trace, 30.0)
        if view.new_lines:
            self.log.configure(state="normal")
            for line in view.new_lines:
                self.log.insert("end", line + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
