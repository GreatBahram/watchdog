# Copyright 2011 Yesudeep Mangalapilly <yesudeep@gmail.com>
# Copyright 2012 Google, Inc & contributors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import contextlib
import queue
import threading
from pathlib import Path

from watchdog.utils import BaseThread
from watchdog.utils.bricks import SkipRepeatsQueue

DEFAULT_EMITTER_TIMEOUT = 1  # in seconds.
DEFAULT_OBSERVER_TIMEOUT = 1  # in seconds.


class EventQueue(SkipRepeatsQueue):
    """Thread-safe event queue based on a special queue that skips adding
    the same event (:class:`FileSystemEvent`) multiple times consecutively.
    Thus avoiding dispatching multiple event handling
    calls when multiple identical events are produced quicker than an observer
    can consume them.
    """


class ObservedWatch:
    """An scheduled watch.

    :param path:
        Path string.
    :param recursive:
        ``True`` if watch is recursive; ``False`` otherwise.
    :param event_filter:
        Optional collection of :class:`watchdog.events.FileSystemEvent` to watch
    """

    def __init__(self, path, recursive, event_filter=None):
        self._path = str(path) if isinstance(path, Path) else path
        self._is_recursive = recursive
        self._event_filter = frozenset(event_filter) if event_filter is not None else None

    @property
    def path(self):
        """The path that this watch monitors."""
        return self._path

    @property
    def is_recursive(self):
        """Determines whether subdirectories are watched for the path."""
        return self._is_recursive

    @property
    def event_filter(self):
        """Collection of event types watched for the path"""
        return self._event_filter

    @property
    def key(self):
        return self.path, self.is_recursive, self.event_filter

    def __eq__(self, watch):
        return self.key == watch.key

    def __ne__(self, watch):
        return self.key != watch.key

    def __hash__(self):
        return hash(self.key)

    def __repr__(self):
        if self.event_filter is not None:
            event_filter_str = "|".join(sorted(_cls.__name__ for _cls in self.event_filter))
            event_filter_str = f", event_filter={event_filter_str}"
        else:
            event_filter_str = ""
        return f"<{type(self).__name__}: path={self.path!r}, is_recursive={self.is_recursive}{event_filter_str}>"


# Observer classes
class EventEmitter(BaseThread):
    """Producer thread base class subclassed by event emitters
    that generate events and populate a queue with them.

    :param event_queue:
        The event queue to populate with generated events.
    :type event_queue:
        :class:`watchdog.events.EventQueue`
    :param watch:
        The watch to observe and produce events for.
    :type watch:
        :class:`ObservedWatch`
    :param timeout:
        Timeout (in seconds) between successive attempts at reading events.
    :type timeout:
        ``float``
    :param event_filter:
        Collection of event types to emit, or None for no filtering (default).
    :type event_filter:
        Optional[Iterable[:class:`watchdog.events.FileSystemEvent`]]
    """

    def __init__(self, event_queue, watch, timeout=DEFAULT_EMITTER_TIMEOUT, event_filter=None):
        super().__init__()
        self._event_queue = event_queue
        self._watch = watch
        self._timeout = timeout
        self._event_filter = frozenset(event_filter) if event_filter is not None else None

    @property
    def timeout(self):
        """Blocking timeout for reading events."""
        return self._timeout

    @property
    def watch(self):
        """The watch associated with this emitter."""
        return self._watch

    def queue_event(self, event):
        """Queues a single event.

        :param event:
            Event to be queued.
        :type event:
            An instance of :class:`watchdog.events.FileSystemEvent`
            or a subclass.
        """
        if self._event_filter is None or any(isinstance(event, cls) for cls in self._event_filter):
            self._event_queue.put((event, self.watch))

    def queue_events(self, timeout):
        """Override this method to populate the event queue with events
        per interval period.

        :param timeout:
            Timeout (in seconds) between successive attempts at
            reading events.
        :type timeout:
            ``float``
        """

    def run(self):
        while self.should_keep_running():
            self.queue_events(self.timeout)


class EventDispatcher(BaseThread):
    """Consumer thread base class subclassed by event observer threads
    that dispatch events from an event queue to appropriate event handlers.

    :param timeout:
        Timeout value (in seconds) passed to emitters
        constructions in the child class BaseObserver.
    :type timeout:
        ``float``
    """

    stop_event = object()
    """Event inserted into the queue to signal a requested stop."""

    def __init__(self, timeout=DEFAULT_OBSERVER_TIMEOUT):
        super().__init__()
        self._event_queue = EventQueue()
        self._timeout = timeout

    @property
    def timeout(self):
        """Timeout value to construct emitters with."""
        return self._timeout

    def stop(self):
        BaseThread.stop(self)
        with contextlib.suppress(queue.Full):
            self.event_queue.put_nowait(EventDispatcher.stop_event)

    @property
    def event_queue(self):
        """The event queue which is populated with file system events
        by emitters and from which events are dispatched by a dispatcher
        thread.
        """
        return self._event_queue

    def dispatch_events(self, event_queue):
        """Override this method to consume events from an event queue, blocking
        on the queue for the specified timeout before raising :class:`queue.Empty`.

        :param event_queue:
            Event queue to populate with one set of events.
        :type event_queue:
            :class:`EventQueue`
        :raises:
            :class:`queue.Empty`
        """

    def run(self):
        while self.should_keep_running():
            try:
                self.dispatch_events(self.event_queue)
            except queue.Empty:
                continue


class BaseObserver(EventDispatcher):
    """Base observer."""

    def __init__(self, emitter_class, timeout=DEFAULT_OBSERVER_TIMEOUT):
        super().__init__(timeout)
        self._emitter_class = emitter_class
        self._lock = threading.RLock()
        self._watches = set()
        self._handlers = {}
        self._emitters = set()
        self._emitter_for_watch = {}

    def _add_emitter(self, emitter):
        self._emitter_for_watch[emitter.watch] = emitter
        self._emitters.add(emitter)

    def _remove_emitter(self, emitter):
        del self._emitter_for_watch[emitter.watch]
        self._emitters.remove(emitter)
        emitter.stop()
        with contextlib.suppress(RuntimeError):
            emitter.join()

    def _clear_emitters(self):
        for emitter in self._emitters:
            emitter.stop()
        for emitter in self._emitters:
            with contextlib.suppress(RuntimeError):
                emitter.join()
        self._emitters.clear()
        self._emitter_for_watch.clear()

    def _add_handler_for_watch(self, event_handler, watch):
        if watch not in self._handlers:
            self._handlers[watch] = set()
        self._handlers[watch].add(event_handler)

    def _remove_handlers_for_watch(self, watch):
        del self._handlers[watch]

    @property
    def emitters(self):
        """Returns event emitter created by this observer."""
        return self._emitters

    def start(self):
        for emitter in self._emitters.copy():
            try:
                emitter.start()
            except Exception:
                self._remove_emitter(emitter)
                raise
        super().start()

    def schedule(self, event_handler, path, recursive=False, event_filter=None):
        """Schedules watching a path and calls appropriate methods specified
        in the given event handler in response to file system events.

        :param event_handler:
            An event handler instance that has appropriate event handling
            methods which will be called by the observer in response to
            file system events.
        :type event_handler:
            :class:`watchdog.events.FileSystemEventHandler` or a subclass
        :param path:
            Directory path that will be monitored.
        :type path:
            ``str``
        :param recursive:
            ``True`` if events will be emitted for sub-directories
            traversed recursively; ``False`` otherwise.
        :type recursive:
            ``bool``
        :param event_filter:
            Collection of event types to emit, or None for no filtering (default).
        :type event_filter:
            Optional[Iterable[:class:`watchdog.events.FileSystemEvent`]]
        :return:
            An :class:`ObservedWatch` object instance representing
            a watch.
        """
        with self._lock:
            watch = ObservedWatch(path, recursive, event_filter)
            self._add_handler_for_watch(event_handler, watch)

            # If we don't have an emitter for this watch already, create it.
            if self._emitter_for_watch.get(watch) is None:
                emitter = self._emitter_class(self.event_queue, watch, timeout=self.timeout, event_filter=event_filter)
                if self.is_alive():
                    emitter.start()
                self._add_emitter(emitter)
            self._watches.add(watch)
        return watch

    def add_handler_for_watch(self, event_handler, watch):
        """Adds a handler for the given watch.

        :param event_handler:
            An event handler instance that has appropriate event handling
            methods which will be called by the observer in response to
            file system events.
        :type event_handler:
            :class:`watchdog.events.FileSystemEventHandler` or a subclass
        :param watch:
            The watch to add a handler for.
        :type watch:
            An instance of :class:`ObservedWatch` or a subclass of
            :class:`ObservedWatch`
        """
        with self._lock:
            self._add_handler_for_watch(event_handler, watch)

    def remove_handler_for_watch(self, event_handler, watch):
        """Removes a handler for the given watch.

        :param event_handler:
            An event handler instance that has appropriate event handling
            methods which will be called by the observer in response to
            file system events.
        :type event_handler:
            :class:`watchdog.events.FileSystemEventHandler` or a subclass
        :param watch:
            The watch to remove a handler for.
        :type watch:
            An instance of :class:`ObservedWatch` or a subclass of
            :class:`ObservedWatch`
        """
        with self._lock:
            self._handlers[watch].remove(event_handler)

    def unschedule(self, watch):
        """Unschedules a watch.

        :param watch:
            The watch to unschedule.
        :type watch:
            An instance of :class:`ObservedWatch` or a subclass of
            :class:`ObservedWatch`
        """
        with self._lock:
            emitter = self._emitter_for_watch[watch]
            del self._handlers[watch]
            self._remove_emitter(emitter)
            self._watches.remove(watch)

    def unschedule_all(self):
        """Unschedules all watches and detaches all associated event
        handlers.
        """
        with self._lock:
            self._handlers.clear()
            self._clear_emitters()
            self._watches.clear()

    def on_thread_stop(self):
        self.unschedule_all()

    def dispatch_events(self, event_queue):
        entry = event_queue.get(block=True)
        if entry is EventDispatcher.stop_event:
            return
        event, watch = entry

        with self._lock:
            # To allow unschedule/stop and safe removal of event handlers
            # within event handlers itself, check if the handler is still
            # registered after every dispatch.
            for handler in list(self._handlers.get(watch, [])):
                if handler in self._handlers.get(watch, []):
                    handler.dispatch(event)
        event_queue.task_done()
