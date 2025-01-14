from __future__ import annotations

import dataclasses
import os
from queue import Empty, Queue
from typing import Optional, Type, Union, Protocol

from watchdog.events import FileSystemEvent
from watchdog.observers.api import EventEmitter, ObservedWatch
from watchdog.utils import platform

Emitter: Type[EventEmitter]

if platform.is_linux():
    from watchdog.observers.inotify import InotifyEmitter as Emitter
    from watchdog.observers.inotify import InotifyFullEmitter
elif platform.is_darwin():
    from watchdog.observers.fsevents import FSEventsEmitter as Emitter
elif platform.is_windows():
    from watchdog.observers.read_directory_changes import WindowsApiEmitter as Emitter
elif platform.is_bsd():
    from watchdog.observers.kqueue import KqueueEmitter as Emitter


class P(Protocol):
    def __call__(self, *args: str) -> str:
        ...


class StartWatching(Protocol):
    def __call__(
        self,
        path: Optional[Union[str, bytes]] = ...,
        use_full_emitter: bool = ...,
        recursive: bool = ...,
    ) -> EventEmitter:
        ...


class ExpectEvent(Protocol):
    def __call__(self, expected_event: FileSystemEvent, timeout: float = ...) -> None:


class ExpectAnyEvent(Protocol):
    def __call__(self, *expected_events: FileSystemEvent, timeout: float = ...) -> None:
        ...

TestEventQueue = Queue[tuple[FileSystemEvent, ObservedWatch]]


@dataclasses.dataclass()
class Helper:
    tmp: str
    emitters: list[EventEmitter] = dataclasses.field(default_factory=list)
    event_queue: TestEventQueue = dataclasses.field(default_factory=Queue)

    def joinpath(self, *args: str) -> str:
        return os.path.join(self.tmp, *args)

    def start_watching(
        self,
        path: Optional[Union[str, bytes]] = None,
        use_full_emitter: bool = False,
        recursive: bool = True,
    ) -> EventEmitter:
        # todo: check if other platforms expect the trailing slash (e.g. `p('')`)
        path = self.tmp if path is None else path

        emitter: EventEmitter
        if platform.is_linux() and use_full_emitter:
            emitter = InotifyFullEmitter(self.event_queue, ObservedWatch(path, recursive=recursive))
        else:
            emitter = Emitter(self.event_queue, ObservedWatch(path, recursive=recursive))

        self.emitters.append(emitter)

        if platform.is_darwin():
            # TODO: I think this could be better...  .suppress_history should maybe
            #       become a common attribute.
            from watchdog.observers.fsevents import FSEventsEmitter

            assert isinstance(emitter, FSEventsEmitter)
            emitter.suppress_history = True

        emitter.start()

        return emitter

    def expect_any_event(self, *expected_events: FileSystemEvent, timeout: float = 2) -> None:
        """Utility function to wait up to `timeout` seconds for any `expected_event`
        for `path` to show up in the queue.

        Provides some robustness for the otherwise flaky nature of asynchronous notifications.
        """
        try:
            event = self.event_queue.get(timeout=timeout)[0]
            assert event in expected_events
        except Empty:
            raise

    def expect_event(self, expected_event: FileSystemEvent, timeout: float = 2) -> None:
        """Utility function to wait up to `timeout` seconds for an `event_type` for `path` to show up in the queue.

        Provides some robustness for the otherwise flaky nature of asynchronous notifications.
        """
        try:
            event = self.event_queue.get(timeout=timeout)[0]
            assert event == expected_event
        except Empty:
            raise

    def close(self) -> None:
        for emitter in self.emitters:
            emitter.stop()

        for emitter in self.emitters:
            if emitter.is_alive():
                emitter.join(5)

        alive = [emitter.is_alive() for emitter in self.emitters]
        self.emitters = []
        assert alive == [False] * len(alive)
