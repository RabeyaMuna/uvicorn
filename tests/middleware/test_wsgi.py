from __future__ import annotations

import io
import sys
from collections.abc import AsyncGenerator
from typing import Callable

import httpx
import pytest

try:
    import a2wsgi
except ModuleNotFoundError:
    a2wsgi = None  # type: ignore

from uvicorn._types import Environ, HTTPRequestEvent, HTTPScope, StartResponse
from uvicorn.middleware import wsgi


def hello_world(environ: Environ, start_response: StartResponse) -> list[bytes]:
    status = "200 OK"
    output = b"Hello World!\n"
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(output))),
    ]
    start_response(status, headers, None)
    return [output]


def echo_body(environ: Environ, start_response: StartResponse) -> list[bytes]:
    status = "200 OK"
    output = environ["wsgi.input"].read()
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(output))),
    ]
    start_response(status, headers, None)
    return [output]


def raise_exception(environ: Environ, start_response: StartResponse) -> list[bytes]:
    raise RuntimeError("Something went wrong")


def return_exc_info(environ: Environ, start_response: StartResponse) -> list[bytes]:
    try:
        raise RuntimeError("Something went wrong")
    except RuntimeError:
        status = "500 Internal Server Error"
        output = b"Internal Server Error"
        headers = [
            ("Content-Type", "text/plain; charset=utf-8"),
            ("Content-Length", str(len(output))),
        ]
        start_response(status, headers, sys.exc_info())  # type: ignore[arg-type]
        return [output]


@pytest.fixture(params=[wsgi._WSGIMiddleware, a2wsgi.WSGIMiddleware] if a2wsgi else [wsgi._WSGIMiddleware])
def wsgi_middleware(request: pytest.FixtureRequest) -> Callable:
    return request.param


@pytest.mark.anyio
async def test_wsgi_get(wsgi_middleware: Callable) -> None:
    transport = httpx.ASGITransport(wsgi_middleware(hello_world))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.text == "Hello World!\n"


@pytest.mark.anyio
async def test_wsgi_post(wsgi_middleware: Callable) -> None:
    transport = httpx.ASGITransport(wsgi_middleware(echo_body))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/", json={"example": 123})
    assert response.status_code == 200
    assert response.text == '{"example":123}'


@pytest.mark.anyio
async def test_wsgi_put_more_body(wsgi_middleware: Callable) -> None:
    async def generate_body() -> AsyncGenerator[bytes, None]:
        for _ in range(1024):
            yield b"123456789abcdef\n" * 64

    transport = httpx.ASGITransport(wsgi_middleware(echo_body))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.put("/", content=generate_body())
    assert response.status_code == 200
    assert response.text == "123456789abcdef\n" * 64 * 1024


@pytest.mark.anyio
async def test_wsgi_exception(wsgi_middleware: Callable) -> None:
    # Note that we're testing the WSGI app directly here.
    # The HTTP protocol implementations would catch this error and return 500.
    transport = httpx.ASGITransport(wsgi_middleware(raise_exception))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        with pytest.raises(RuntimeError):
            await client.get("/")


@pytest.mark.anyio
async def test_wsgi_exc_info(wsgi_middleware: Callable) -> None:
    app = wsgi_middleware(return_exc_info)
    transport = httpx.ASGITransport(
        app=app,
        raise_app_exceptions=False,
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_build_environ_encoding() -> None:
    scope: HTTPScope = {
        "asgi": {"version": "3.0", "spec_version": "2.0"},
        "scheme": "http",
        "raw_path": b"/\xe6\x96\x87%2Fall",
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "path": "/文/all",
        "root_path": "/文",
        "client": None,
        "server": None,
        "query_string": b"a=123&b=456",
        "headers": [(b"key", b"value1"), (b"key", b"value2")],
        "extensions": {},
    }
    message: HTTPRequestEvent = {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }
    environ = wsgi.build_environ(scope, message, io.BytesIO(b""))
    assert environ["SCRIPT_NAME"] == "/文".encode().decode("latin-1")
    assert environ["PATH_INFO"] == b"/all".decode("latin-1")
    assert environ["HTTP_KEY"] == "value1,value2"


def delayed_hello_world(environ: Environ, start_response: StartResponse) -> list[bytes]:
    import time
    time.sleep(0.1)  # Delay to allow sender to hit empty queue case
    status = "200 OK"
    output = b"Hello World!\n"
    headers = [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(output))),
    ]
    start_response(status, headers, None)
    return [output]


@pytest.mark.anyio
async def test_wsgi_sender_empty_queue() -> None:
    # Test the native _WSGIMiddleware specifically to cover the sender's
    # empty queue case (lines 154-155 in wsgi.py)
    # The sender should wait on the event when the queue is initially empty
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        transport = httpx.ASGITransport(wsgi._WSGIMiddleware(delayed_hello_world))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/")
    assert response.status_code == 200
    assert response.text == "Hello World!\n"
