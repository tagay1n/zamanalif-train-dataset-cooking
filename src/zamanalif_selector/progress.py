from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


class NullProgress:
    def __enter__(self) -> "NullProgress":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def add_task(self, description: str, *, total: int | None = None, **fields: object) -> int:
        return 0

    def advance(self, task_id: int, advance: int = 1, **fields: object) -> None:
        return None

    def update(self, task_id: int, **fields: object) -> None:
        return None

    @contextmanager
    def status(self, message: str) -> Iterator[None]:
        yield


class RichCliProgress:
    def __init__(self) -> None:
        try:
            from rich.console import Console
            from rich.progress import (
                BarColumn,
                Progress,
                SpinnerColumn,
                TaskProgressColumn,
                TextColumn,
            )
            from rich.table import Column
        except ImportError as exc:
            raise SystemExit("Install project dependencies before using progress output.") from exc

        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn(
                "[progress.description]{task.description}",
                table_column=Column(width=20, no_wrap=True, overflow="ellipsis"),
            ),
            BarColumn(bar_width=28),
            TaskProgressColumn(),
            TextColumn(
                "[dim]{task.fields[summary]}",
                table_column=Column(max_width=40, no_wrap=True, overflow="ellipsis"),
            ),
            console=self._console,
            transient=False,
        )

    def __enter__(self) -> "RichCliProgress":
        self._progress.__enter__()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._progress.__exit__(exc_type, exc, traceback)

    def add_task(self, description: str, *, total: int | None = None, **fields: object) -> int:
        fields["summary"] = _summary_text(fields.get("summary", ""))
        return self._progress.add_task(description, total=total, **fields)

    def advance(self, task_id: int, advance: int = 1, **fields: object) -> None:
        fields["summary"] = _summary_text(fields.get("summary", ""))
        self._progress.update(task_id, advance=advance, **fields)

    def update(self, task_id: int, **fields: object) -> None:
        fields["summary"] = _summary_text(fields.get("summary", ""))
        self._progress.update(task_id, **fields)

    @contextmanager
    def status(self, message: str) -> Iterator[None]:
        task_id = self.add_task(message, total=None)
        try:
            yield
        finally:
            self._progress.remove_task(task_id)


def cli_progress(*, quiet: bool) -> NullProgress | RichCliProgress:
    if quiet:
        return NullProgress()
    return RichCliProgress()


def _summary_text(value: object) -> str:
    text = str(value or "")
    if len(text) <= 96:
        return text
    return text[:93] + "..."
