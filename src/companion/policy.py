"""Backend policy gates. No sensors, inference or personal-content storage."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Callable, TypeVar

T = TypeVar("T")


class State(str, Enum):
    ACTIVE = "ACTIVE"
    STOPPING = "STOPPING"
    PRIVATE = "PRIVATE"
    ERROR = "ERROR"


class Denied(PermissionError):
    """An operation is outside the current authorization."""


@dataclass(frozen=True)
class Lease:
    owner: object
    epoch: int
    operation: str
    restricted: bool


class Policy:
    """All side effects must pass publish() immediately before execution.

    The state file contains only the private-mode latch, never personal content.
    A caller must choose an OS user-data path outside docs/source repositories.
    Stop callbacks must release resources; failures leave admission closed.
    """

    def __init__(self, state_path: Path | None = None) -> None:
        self._lock = RLock()
        self._owner = object()
        self._epoch = 0
        self._grants: set[str] = set()
        self._memory = False
        self._intimate = False
        self._stoppers: list[Callable[[], None]] = []
        self._state_path = state_path
        self._state = State.ACTIVE
        if state_path is not None and state_path.exists():
            try:
                saved = json.loads(state_path.read_text(encoding="utf-8"))
                if type(saved.get("private")) is not bool:
                    raise ValueError("invalid private latch")
                if saved["private"]:
                    self._state = State.PRIVATE
            except (OSError, ValueError, TypeError, AttributeError):
                self._state = State.ERROR

    @property
    def state(self) -> State:
        with self._lock:
            return self._state

    def register_stopper(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if self._state is not State.ACTIVE:
                raise Denied("runtime is blocked")
            self._stoppers.append(callback)

    def grant(self, operation: str) -> None:
        with self._lock:
            if self._state is not State.ACTIVE:
                raise Denied("runtime is blocked")
            self._grants.add(operation)

    def revoke(self, operation: str) -> None:
        with self._lock:
            if operation in self._grants:
                self._grants.discard(operation)
                self._epoch += 1

    def set_memory(self, enabled: bool) -> None:
        with self._lock:
            if self._state is not State.ACTIVE:
                raise Denied("runtime is blocked")
            self._memory = enabled
            self._epoch += 1

    def open_intimacy(self, *, local_action: bool) -> None:
        # The future UI/auth adapter owns verification of the local action.
        with self._lock:
            if not local_action or self._state is not State.ACTIVE:
                raise Denied("explicit local action required")
            self._intimate = True

    def close_intimacy(self) -> None:
        with self._lock:
            self._intimate = False
            self._epoch += 1

    def begin(self, operation: str, *, restricted: bool = False) -> Lease:
        with self._lock:
            self._authorize(operation, restricted)
            return Lease(self._owner, self._epoch, operation, restricted)

    def _authorize(self, operation: str, restricted: bool) -> None:
        if self._state is not State.ACTIVE:
            raise Denied("runtime is blocked")
        if operation not in self._grants:
            raise Denied("operation is not authorized")
        if operation in {"memory.read", "memory.write"} and not self._memory:
            raise Denied("memory is disabled")
        if restricted and not self._intimate:
            raise Denied("restricted access is closed")

    def publish(self, lease: Lease, effect: Callable[[], T]) -> T:
        """Atomically revalidate and execute a short publication/commit.

        Long operations belong outside this callback. Their returned results
        must pass this gate. This cannot recall data already sent externally.
        """
        with self._lock:
            if lease.owner is not self._owner or lease.epoch != self._epoch:
                raise Denied("authorization has changed")
            self._authorize(lease.operation, lease.restricted)
            return effect()

    def _save_latch(self, private: bool) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(self._state_path.name + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump({"private": private}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def enter_private(self) -> None:
        with self._lock:
            if self._state is State.PRIVATE:
                return
            if self._state is State.STOPPING:
                raise Denied("stop is already in progress")
            self._state = State.STOPPING
            self._epoch += 1
            self._intimate = False
            try:
                self._save_latch(True)
            except OSError:
                # Still attempt resource shutdown, while remaining fail-closed.
                latch_failed = True
            else:
                latch_failed = False
            stoppers = tuple(self._stoppers)
        failed = latch_failed
        for stop in stoppers:
            try:
                stop()
            except Exception:
                failed = True
        with self._lock:
            self._state = State.ERROR if failed else State.PRIVATE
        if failed:
            raise RuntimeError("private shutdown incomplete; runtime stays blocked")

    def resume(self, *, local_action: bool) -> None:
        with self._lock:
            if not local_action or self._state is not State.PRIVATE:
                raise Denied("local resume from PRIVATE required")
            try:
                self._save_latch(False)
            except OSError:
                self._state = State.ERROR
                raise
            self._intimate = False
            self._epoch += 1
            self._state = State.ACTIVE
