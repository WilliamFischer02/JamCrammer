"""Bus → Qt signal bridge. Worker threads publish; the GUI thread receives queued signals."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from jamcrammer.core.events import Event, EventBus


class QtBus(QObject):
    received = Signal(object)  # Event

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsubscribe = bus.subscribe("*", self._forward)

    def _forward(self, event: Event) -> None:
        self.received.emit(event)

    def detach(self) -> None:
        self._unsubscribe()
