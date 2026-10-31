"""Lectura de Server-Sent Events, compartida por los adaptadores de proveedores que hacen streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator

import httpx


class SseDecoder:
    """Agrupa líneas Server-Sent Events en (event, data), línea a línea. Ignora los comentarios."""

    def __init__(self) -> None:
        self._event: str | None = None
        self._data: list[str] = []

    def feed(self, line: str) -> tuple[str | None, str] | None:
        if line == "":
            return self.flush()
        if not line.startswith(":"):
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                self._event = value
            elif name == "data":
                self._data.append(value)
        return None

    def flush(self) -> tuple[str | None, str] | None:
        event = (self._event, "\n".join(self._data)) if self._data else None
        self._event, self._data = None, []
        return event


def parse_sse(lines: Iterable[str]) -> Iterator[tuple[str | None, str]]:
    decoder = SseDecoder()
    for line in lines:
        if (event := decoder.feed(line)) is not None:
            yield event
    if (event := decoder.flush()) is not None:
        yield event


async def sse_events(response: httpx.Response) -> AsyncIterator[tuple[str | None, str]]:
    decoder = SseDecoder()
    async for line in response.aiter_lines():
        if (event := decoder.feed(line)) is not None:
            yield event
    if (event := decoder.flush()) is not None:
        yield event
