"""Test doubles for app.scripts.fetch_model: a network guard, and a hub that serves real byte ranges in memory.

`forbid_network` is what keeps the download tests honest. It raises through pytest.fail, a BaseException, because
download() and main() catch Exception: an ordinary error would be retried and end as a plain "return 1".
"""

from __future__ import annotations

import re
import socket
from collections.abc import Iterable, Iterator

import httpx
import pytest

from app.scripts import fetch_model

PIECE_BYTES = 64 * 1024
CDN_HOST = "cdn.test"


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any real httpx transport, and any name lookup or outgoing socket helper, fails the test."""

    def refuse(*_args: object, **_kwargs: object) -> None:
        pytest.fail("a test tried to reach the real network", pytrace=False)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    for name in ("HF_ENDPOINT", "HF_TOKEN"):
        monkeypatch.delenv(name, raising=False)


class CutStream(httpx.SyncByteStream):
    """A response body that arrives in small pieces and, when `cut_after` is set, dies after that many bytes."""

    def __init__(self, body: bytes, cut_after: int | None) -> None:
        self.body = body
        self.cut_after = cut_after

    def __iter__(self) -> Iterator[bytes]:
        end = len(self.body) if self.cut_after is None else min(self.cut_after, len(self.body))
        for start in range(0, end, PIECE_BYTES):
            yield self.body[start:min(start + PIECE_BYTES, end)]
        if self.cut_after is not None:
            raise httpx.ReadError("connection reset by peer")


class Hub:
    """The file side of the Hub, for httpx.MockTransport. Bodies really are cut to the requested range.

    `cuts` scripts the file requests in order: an int kills that response after so many bytes, None serves it whole.
    `list_errors` are raised, in order, by the stand-in for fetch_model.list_files.
    """

    def __init__(
        self,
        files: dict[str, bytes],
        *,
        cuts: Iterable[int | None] = (),
        list_errors: Iterable[BaseException] = (),
        honour_range: bool = True,
        status: int | None = None,
        redirect: bool = False,
        listed_sizes: dict[str, int] | None = None,
        listed_hashes: dict[str, str | None] | None = None,
    ) -> None:
        self.files = dict(files)
        self.cuts = list(cuts)
        self.list_errors = list(list_errors)
        self.honour_range = honour_range
        self.status = status
        self.redirect = redirect
        self.listed_sizes = listed_sizes or {}
        self.listed_hashes = listed_hashes or {}
        self.requests: list[httpx.Request] = []
        self.list_calls: list[tuple[str, str]] = []

    @property
    def served(self) -> list[httpx.Request]:
        """Requests that reached the bytes, leaving out the hop that only answered with a redirect."""
        return [r for r in self.requests if not self.redirect or r.url.host == CDN_HOST]

    @property
    def ranges(self) -> list[str | None]:
        return [r.headers.get("Range") for r in self.served]

    def list_files(self, repo: str, revision: str) -> list[tuple[str, int, str | None]]:
        self.list_calls.append((repo, revision))
        if self.list_errors:
            raise self.list_errors.pop(0)
        return [
            (name, self.listed_sizes.get(name, len(body)), self.listed_hashes.get(name))
            for name, body in self.files.items()
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.redirect and request.url.host != CDN_HOST:
            return httpx.Response(302, headers={"Location": str(request.url.copy_with(host=CDN_HOST))})
        if self.status is not None:
            return httpx.Response(self.status, content=b"refused")
        body = self.files[request.url.path.rsplit("/", 1)[-1]]
        cut_after = self.cuts.pop(0) if self.cuts else None
        match = re.fullmatch(r"bytes=(\d+)-", request.headers.get("Range", ""))
        if not (match and self.honour_range and body):
            return httpx.Response(200, stream=CutStream(body, cut_after))
        start = int(match[1])
        if start >= len(body):
            return httpx.Response(416, headers={"Content-Range": f"bytes */{len(body)}"})
        content_range = f"bytes {start}-{len(body) - 1}/{len(body)}"
        return httpx.Response(206, headers={"Content-Range": content_range}, stream=CutStream(body[start:], cut_after))


def serve(monkeypatch: pytest.MonkeyPatch, hub: Hub) -> Hub:
    """Point fetch_model at `hub`: list_files is replaced, and every httpx.Client it builds gets a MockTransport."""

    class MockedClient(httpx.Client):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, transport=httpx.MockTransport(hub), **kwargs)

    monkeypatch.setattr(httpx, "Client", MockedClient)
    monkeypatch.setattr(fetch_model, "list_files", hub.list_files)
    return hub
