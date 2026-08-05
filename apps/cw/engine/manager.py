"""The audio-engine seam.

Two families of engine implement one event contract:

* `AudioDemodulator` — consumes audio blocks and decodes them itself. The CW
  decoder is the first; an ML decoder later is a drop-in sibling.
* `NetworkTapEngine` — produces the same events from an *external* decoder over
  IPC (fldigi XML-RPC, WSJT-X UDP). No audio flows through it; it just adapts a
  foreign text stream into our event contract.

`AudioEngineManager` owns the active audio source, fans blocks out to every
attached `AudioDemodulator`, runs `NetworkTapEngine`s alongside, and pushes all
resulting `CharEvent`s to subscribers (the log bridge, the live view). Adding a
new mode later = writing one class + registering it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Callable

import numpy as np
from numpy.typing import NDArray

from .events import CharEvent

if TYPE_CHECKING:
    from .sources import AudioSource

FloatArray = NDArray[np.float32]
Subscriber = Callable[[CharEvent], None]


class Engine(ABC):
    name: str = "engine"

    @abstractmethod
    def reset(self) -> None: ...


class AudioDemodulator(Engine):
    """An engine that decodes from raw audio samples."""

    @abstractmethod
    def process(self, samples: FloatArray) -> list[CharEvent]:
        """Consume a chunk of float audio; return characters completed now."""
        ...

    def flush(self) -> list[CharEvent]:
        """Flush any pending symbol at end of stream / when sending stops.
        Default no-op; audio decoders override."""
        return []


class NetworkTapEngine(Engine):
    """An engine fed by an external process (fldigi/WSJT-X). Stubbed so the
    architecture is explicit; wire real IPC in when those integrations land."""

    def __init__(self, name: str) -> None:
        self.name = name

    def reset(self) -> None:
        pass

    def poll(self) -> list[CharEvent]:
        """Return any events the external decoder produced since last poll."""
        return []


class AudioEngineManager:
    def __init__(self, sample_rate: int) -> None:
        self.sample_rate = sample_rate
        self._demods: list[AudioDemodulator] = []
        self._taps: list[NetworkTapEngine] = []
        self._subs: list[Subscriber] = []

    def add_demodulator(self, demod: AudioDemodulator) -> AudioEngineManager:
        self._demods.append(demod)
        return self

    def add_tap(self, tap: NetworkTapEngine) -> AudioEngineManager:
        self._taps.append(tap)
        return self

    def subscribe(self, fn: Subscriber) -> AudioEngineManager:
        self._subs.append(fn)
        return self

    def _emit(self, events: list[CharEvent]) -> None:
        for ev in events:
            for fn in self._subs:
                fn(ev)

    def pump(self, samples: FloatArray) -> None:
        """Push one chunk of audio through every audio demodulator."""
        for d in self._demods:
            self._emit(d.process(samples))
        for t in self._taps:
            self._emit(t.poll())

    def finalize(self) -> None:
        """Flush every engine (a finite stream has ended)."""
        for d in self._demods:
            self._emit(d.flush())
        for t in self._taps:
            self._emit(t.poll())

    def run_source(self, source: AudioSource) -> None:
        """Drive the manager from any AudioSource until it's exhausted."""
        for block in source.blocks():
            self.pump(block)
        self.finalize()
