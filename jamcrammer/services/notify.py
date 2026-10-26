"""Notifications (KICKOFF §7.5): Windows toast, sound, ntfy.sh push, Discord webhook.

Each channel is a toggle with its own minimum level. Network calls run on a worker thread so
callers never block; ``test_all`` runs synchronously and reports per-channel results.
"""

from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jamcrammer.core.config import Level, NotifyConfig
from jamcrammer.core.events import Event, EventBus
from jamcrammer.core.logging import get_logger

log = get_logger("notify")

APP_ID = "JamCrammer"
CHANNELS = ("toast", "sound", "ntfy", "discord")
TOAST_DURATION_HIGH = "long"
TOAST_DURATION_NOTICE = "short"
SOUND_ALIAS_HIGH = "SystemExclamation"
SOUND_ALIAS_NOTICE = "SystemAsterisk"
NTFY_PRIORITY = {Level.INFO: "low", Level.NOTICE: "default", Level.HIGH: "high"}
NTFY_TAGS = {Level.INFO: "information_source", Level.NOTICE: "bell", Level.HIGH: "rotating_light"}
WORKER_IDLE_S = 0.25
PAYLOAD_PREVIEW_CHARS = 300

Sender = Callable[[Level, str, str, Path | None], None]


@dataclass(frozen=True)
class ChannelResult:
    channel: str
    enabled: bool
    ok: bool
    error: str = ""
    ms: float = 0.0


class Notifier:
    def __init__(
        self,
        cfg: NotifyConfig,
        icon: Path | None = None,
        senders: dict[str, Sender] | None = None,
    ) -> None:
        self.cfg = cfg
        self.icon = icon
        self._senders: dict[str, Sender] = senders or {
            "toast": self._send_toast,
            "sound": self._send_sound,
            "ntfy": self._send_ntfy,
            "discord": self._send_discord,
        }
        self._queue: queue.Queue[tuple[Level, str, str, Path | None]] = queue.Queue()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None
        self.sent: list[tuple[str, Level, str]] = []

    # --- lifecycle ---------------------------------------------------------------------

    def start(self) -> None:
        if self._worker is None:
            self._worker = threading.Thread(target=self._loop, name="notify", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                level, title, message, shot = self._queue.get(timeout=WORKER_IDLE_S)
            except queue.Empty:
                continue
            self.deliver(level, title, message, shot)

    # --- routing -----------------------------------------------------------------------

    def enabled(self, channel: str) -> bool:
        c = self.cfg
        return {
            "toast": c.toast,
            "sound": c.sound,
            "ntfy": bool(c.ntfy_topic),
            "discord": bool(c.discord_webhook),
        }[channel]

    def min_level(self, channel: str) -> Level:
        lv = self.cfg.levels
        return {"toast": lv.toast, "sound": lv.sound, "ntfy": lv.push, "discord": lv.push}[channel]

    def channels_for(self, level: Level) -> list[str]:
        return [ch for ch in CHANNELS if self.enabled(ch) and level.rank >= self.min_level(ch).rank]

    def notify(
        self, level: Level, title: str, message: str, screenshot: Path | None = None
    ) -> None:
        """Queue a notification (non-blocking). Starts the worker on first use."""
        if not self.channels_for(level):
            return
        self.start()
        self._queue.put((level, title, message, screenshot))

    def deliver(
        self, level: Level, title: str, message: str, screenshot: Path | None = None
    ) -> list[ChannelResult]:
        """Send synchronously on every channel that accepts ``level``."""
        results = []
        for ch in self.channels_for(level):
            results.append(self._deliver_one(ch, level, title, message, screenshot))
        return results

    def test_all(self) -> list[ChannelResult]:
        """Send a test message on every enabled channel regardless of level thresholds."""
        results = []
        for ch in CHANNELS:
            if not self.enabled(ch):
                results.append(ChannelResult(ch, enabled=False, ok=False, error="disabled"))
                continue
            results.append(
                self._deliver_one(
                    ch, Level.HIGH, "JamCrammer test", f"Test notification on {ch}.", None
                )
            )
        return results

    def _deliver_one(
        self, ch: str, level: Level, title: str, message: str, shot: Path | None
    ) -> ChannelResult:
        t0 = time.perf_counter()
        try:
            self._senders[ch](level, title, message, shot)
            self.sent.append((ch, level, title))
            return ChannelResult(ch, True, True, ms=(time.perf_counter() - t0) * 1000.0)
        except Exception as exc:
            log.warning("notify %s failed: %s", ch, exc)
            return ChannelResult(
                ch, True, False, f"{type(exc).__name__}: {exc}", (time.perf_counter() - t0) * 1000.0
            )

    def attach(self, bus: EventBus) -> Callable[[], None]:
        """Forward every NOTICE/HIGH event on the bus as a notification."""

        def on_event(event: Event) -> None:
            if event.level.rank < Level.NOTICE.rank:
                return
            title = f"JamCrammer {event.level}: {event.type}"
            body = event.payload.get("message") or _summarize(event)
            self.notify(event.level, title, str(body), event.screenshot_path)

        return bus.subscribe("*", on_event)

    # --- channels ----------------------------------------------------------------------

    def _send_toast(self, level: Level, title: str, message: str, _shot: Path | None) -> None:
        from winotify import Notification, audio

        toast = Notification(
            app_id=APP_ID,
            title=title,
            msg=message,
            icon=str(self.icon) if self.icon else "",
            duration=TOAST_DURATION_HIGH if level == Level.HIGH else TOAST_DURATION_NOTICE,
        )
        toast.set_audio(audio.Silent, loop=False)  # sound is its own channel
        toast.show()

    def _send_sound(self, level: Level, _title: str, _message: str, _shot: Path | None) -> None:
        import winsound

        alias = SOUND_ALIAS_HIGH if level == Level.HIGH else SOUND_ALIAS_NOTICE
        repeats = self.cfg.sound_repeats if level == Level.HIGH else 1
        for i in range(repeats):
            winsound.PlaySound(alias, winsound.SND_ALIAS)
            if i + 1 < repeats:
                time.sleep(self.cfg.sound_interval_s)

    def _send_ntfy(self, level: Level, title: str, message: str, _shot: Path | None) -> None:
        import requests

        url = f"{self.cfg.ntfy_server.rstrip('/')}/{self.cfg.ntfy_topic}"
        headers = {
            "Title": title.encode("ascii", "replace").decode(),
            "Priority": NTFY_PRIORITY[level],
            "Tags": NTFY_TAGS[level],
        }
        r = requests.post(
            url, data=message.encode("utf-8"), headers=headers, timeout=self.cfg.timeout_s
        )
        r.raise_for_status()

    def _send_discord(self, level: Level, title: str, message: str, shot: Path | None) -> None:
        import requests

        content = f"**{title}**\n{message}"
        if shot is not None and shot.exists():
            with shot.open("rb") as fh:
                r = requests.post(
                    self.cfg.discord_webhook,
                    data={"content": content},
                    files={"file": (shot.name, fh, "image/png")},
                    timeout=self.cfg.timeout_s,
                )
        else:
            r = requests.post(
                self.cfg.discord_webhook, json={"content": content}, timeout=self.cfg.timeout_s
            )
        r.raise_for_status()


def _summarize(event: Event) -> str:
    parts = [f"source={event.source}"]
    for k, v in event.payload.items():
        parts.append(f"{k}={v}")
    s = " ".join(parts)
    return s[:PAYLOAD_PREVIEW_CHARS]
