from __future__ import annotations

import fcntl
from pathlib import Path
from types import TracebackType
from typing import IO


class ProcessLock:
    """Non-blocking cross-process lock for one local data writer."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.handle: IO[str] | None = None

    def __enter__(self) -> ProcessLock:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            raise RuntimeError(f"another writer holds {self.path}") from None
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None
