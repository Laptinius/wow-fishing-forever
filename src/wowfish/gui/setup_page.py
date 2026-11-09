"""Setup tab: keys, fishing-bar instructions, audio meter and bite check."""

from __future__ import annotations

from typing import Any

from wowfish.gui.presenter import Presenter, VerifyDone, View

CHECKLIST = (
    "Перед запуском:\n"
    "  1. Встань у воды и открой Forever Fishing.\n"  # noqa: RUF001
    "  2. Проверь, что выбранные клавиши забрасывают удочку и подсекают.\n"
    "  3. Включи автоматический сбор добычи (Auto Loot).\n"  # noqa: RUF001
    "  4. Оставь центральную полоску рыбалки видимой, звук эффектов — включённым."
)


class SetupPage:
    def __init__(self, parent: Any, presenter: Presenter) -> None:
        import ttkbootstrap as tb

        self.p = presenter
        self.frame = tb.Frame(parent, padding=10)
        f = self.frame
        tb.Label(f, text=CHECKLIST, justify="left").pack(anchor="w", pady=(0, 10))

        keys_row = tb.Labelframe(f, text="Keys", padding=8)
        keys_row.pack(fill="x", pady=4)
        tb.Label(keys_row, text="Cast key").grid(row=0, column=0, sticky="w")
        self.cast = tb.Entry(keys_row, width=8)
        self.cast.insert(0, presenter.cfg.game.cast_key)
        self.cast.grid(row=0, column=1, padx=6)
        tb.Label(keys_row, text="Interact key").grid(row=0, column=2, sticky="w")
        self.interact = tb.Entry(keys_row, width=8)
        self.interact.insert(0, presenter.cfg.game.interact_key)
        self.interact.grid(row=0, column=3, padx=6)
        tb.Button(keys_row, text="Save keys", command=self._save_keys, bootstyle="secondary").grid(
            row=0, column=4, padx=6
        )
        self.keys_note = tb.Label(keys_row, text="")
        self.keys_note.grid(row=0, column=5, sticky="w")

        audio = tb.Labelframe(f, text="Audio (what the bot hears)", padding=8)
        audio.pack(fill="x", pady=4)
        self.devices = presenter.audio_devices()
        names = [d.name for d in self.devices]
        self.device = tb.Combobox(audio, values=names, state="readonly", width=48)
        current = presenter.cfg.audio.device
        pick = next(
            (i for i, d in enumerate(self.devices) if current and current in (d.id, d.name)), None
        )
        if pick is None:
            default = presenter.default_device()
            pick = self.devices.index(default) if default in self.devices else None
        if pick is not None:
            self.device.current(pick)
        self.device.grid(row=0, column=0, columnspan=3, sticky="w")
        tb.Button(
            audio, text="Use this device", command=self._save_device, bootstyle="secondary"
        ).grid(row=0, column=3, padx=6)
        tb.Label(audio, text="Level").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.meter = tb.Progressbar(audio, maximum=100, value=0, bootstyle="info", length=300)
        self.meter.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        self.level_text = tb.Label(audio, text="silent")
        self.level_text.grid(row=1, column=3, sticky="w", padx=6, pady=(8, 0))
        tb.Button(
            audio, text="Record 60 s and check the bite", command=self._verify, bootstyle="info"
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.verify_note = tb.Label(
            audio, text="Fish by hand while it records; it needs at least one bite."
        )
        self.verify_note.grid(row=2, column=1, columnspan=3, sticky="w", padx=6, pady=(8, 0))
        bite_row = tb.Frame(audio)
        bite_row.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        tb.Label(bite_row, text="Bite threshold").pack(side="left")
        self.bite = tb.Entry(bite_row, width=6)
        self.bite.insert(0, f"{presenter.cfg.audio.bite_threshold:.2f}")
        self.bite.pack(side="left", padx=6)
        self.bite.bind("<Return>", lambda _e: self._save_bite())
        tb.Button(bite_row, text="Save", command=self._save_bite, bootstyle="success").pack(
            side="left"
        )
        self.bite_note = tb.Label(bite_row, text="A splash must score at least this to count.")
        self.bite_note.pack(side="left", padx=6)

        tb.Label(
            f,
            text="Полоска рыбалки распознаётся автоматически: Fishing / Рыболовля.\n"
            "Держи её видимой. Шестерёнка и положение поплавка не нужны.",
            justify="left",
        ).pack(anchor="w", pady=12)

    # -- actions ----------------------------------------------------------------------------

    def _save_keys(self) -> None:
        try:
            self.p.save_keys(self.cast.get(), self.interact.get())
            self.keys_note.configure(text="saved")
        except ValueError as e:
            self.keys_note.configure(text=str(e))

    def _selected_device(self) -> str | None:
        i = self.device.current()
        return self.devices[i].id if 0 <= i < len(self.devices) else None

    def _save_device(self) -> None:
        sel = self._selected_device()
        if sel is not None:
            self.p.save_audio_device(sel)
            self.p.start_meter(sel)

    def _verify(self) -> None:
        self.p.stop_meter()
        self.verify_note.configure(text="Recording 60 s... fish by hand and let a fish bite.")
        self.p.record_and_verify(60.0, self._selected_device())

    def _save_bite(self) -> None:
        try:
            saved = self.p.save_bite_threshold(self.bite.get())
        except ValueError as e:
            self.bite_note.configure(text=str(e))
            return
        self.bite_note.configure(text=f"saved {saved:.2f}; used from the next Start")

    # -- rendering --------------------------------------------------------------------------

    def render(self, view: View) -> None:
        pct = max(0.0, min(100.0, (view.level_dbfs + 60.0) / 60.0 * 100.0))
        self.meter.configure(value=pct)
        best = max(view.last_scores.values(), default=-1.0)
        level = "silent" if view.level_dbfs <= -60 else f"{view.level_dbfs:5.1f} dBFS"
        self.level_text.configure(text=f"{level}   best bite score {best:.2f}")
        for item in view.log_events:
            if isinstance(item, VerifyDone):
                self.verify_note.configure(text=view.new_lines[view.log_events.index(item)])
                if item.report.stands_out:  # suggest, but only Save changes the config
                    self.bite.delete(0, "end")
                    self.bite.insert(0, f"{item.report.threshold:.2f}")
                    self.bite_note.configure(text="suggested by the bite check; Save to use it")
