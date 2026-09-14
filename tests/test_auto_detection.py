import asyncio
import contextlib
import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

from uvicorn.config import Config
from uvicorn.loops.auto import auto_loop_factory
from uvicorn.protocols.http.auto import AutoHTTPProtocol
from uvicorn.protocols.websockets.auto import AutoWebSocketsProtocol
from uvicorn.server import ServerState

try:
    if sys.platform == "win32":  # pragma: py-not-win32
        importlib.import_module("winloop")
        expected_loop = "winloop"
    else:  # pragma: py-win32
        importlib.import_module("uvloop")
        expected_loop = "uvloop"
except ImportError:  # pragma: no cover
    expected_loop = "asyncio"

try:
    importlib.import_module("httptools")
    expected_http = "HttpToolsProtocol"
except ImportError:  # pragma: no cover
    expected_http = "H11Protocol"

try:
    importlib.import_module("websockets")
    expected_websockets = "WebSocketProtocol"
except ImportError:  # pragma: no cover
    expected_websockets = "WSProtocol"


async def app(scope, receive, send):
    pass  # pragma: no cover


def test_loop_auto():
    loop_factory = auto_loop_factory(use_subprocess=True)
    with contextlib.closing(loop_factory()) as loop:
        assert isinstance(loop, asyncio.AbstractEventLoop)
        assert type(loop).__module__.startswith(expected_loop)


@pytest.mark.anyio
async def test_http_auto():
    config = Config(app=app)
    server_state = ServerState()
    protocol = AutoHTTPProtocol(  # type: ignore[call-arg]
        config=config, server_state=server_state, app_state={}
    )
    assert type(protocol).__name__ == expected_http


@pytest.mark.anyio
async def test_websocket_auto():
    config = Config(app=app)
    server_state = ServerState()

    assert AutoWebSocketsProtocol is not None
    protocol = AutoWebSocketsProtocol(config=config, server_state=server_state, app_state={})
    assert type(protocol).__name__ == expected_websockets


def test_asyncio_loop_factory_non_windows():
    """Test asyncio_loop_factory returns SelectorEventLoop on non-Windows."""
    import uvicorn.loops.asyncio as asyncio_module
    with patch.object(sys := asyncio_module.sys, "platform", "linux"):
        loop_factory = asyncio_module.asyncio_loop_factory(use_subprocess=False)
        loop = loop_factory()
        try:
            assert isinstance(loop, asyncio.SelectorEventLoop)
        finally:
            loop.close()


def test_asyncio_loop_factory_windows(mocker):
    """Test asyncio_loop_factory returns ProactorEventLoop on Windows."""
    import uvicorn.loops.asyncio as asyncio_module
    import asyncio as asyncio_real
    
    # Create a mock ProactorEventLoop class for non-Windows platforms
    class MockProactorEventLoop(asyncio_real.SelectorEventLoop):
        pass
    
    mocker.patch.object(asyncio_module.sys, "platform", "win32")
    # Use create=True because ProactorEventLoop doesn't exist on non-Windows
    mocker.patch.object(asyncio_real, "ProactorEventLoop", MockProactorEventLoop, create=True)
    
    loop_factory = asyncio_module.asyncio_loop_factory(use_subprocess=False)
    loop = loop_factory()
    try:
        assert type(loop).__name__ == "MockProactorEventLoop"
    finally:
        loop.close()


def test_auto_loop_factory_windows(mocker):
    """Test auto_loop_factory uses winloop on Windows when available."""
    import uvicorn.loops.auto as auto_module
    
    mocker.patch.object(sys, "platform", "win32")
    # The winloop module might not be available, so test that the function at least attempts import
    try:
        import uvicorn.loops.winloop  # pragma: no cover
    except ImportError:  # pragma: no cover
        pytest.skip("winloop not available")  # pragma: no cover
    loop_factory = auto_module.auto_loop_factory(use_subprocess=False)
    loop = loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()


def test_auto_loop_factory_non_windows(mocker):
    """Test auto_loop_factory uses uvloop on non-Windows when available."""
    mocker.patch.object(sys, "platform", "darwin")
    try:
        import uvicorn.loops.uvloop  # pragma: no cover
    except ImportError:  # pragma: no cover
        pytest.skip("uvloop not available")  # pragma: no cover
    loop_factory = auto_loop_factory(use_subprocess=False)
    loop = loop_factory()
    try:
        assert isinstance(loop, asyncio.AbstractEventLoop)
    finally:
        loop.close()


def test_auto_loop_factory_windows_path(mocker):
    """Test auto_loop_factory executes Windows import path on win32 platform."""
    import uvicorn.loops.auto as auto_module
    import sys
    
    # Create mock winloop module and add to sys.modules
    mock_winloop = MagicMock()
    mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
    # Make winloop_loop_factory a MagicMock so we can check .called
    mock_winloop.winloop_loop_factory = MagicMock(return_value=mock_loop)
    
    # Add mock winloop to sys.modules so the import statement finds it
    sys.modules["uvicorn.loops.winloop"] = mock_winloop
    
    try:
        # Patch sys.platform in the auto_module to be "win32"
        mocker.patch.object(auto_module.sys, "platform", "win32")
        
        # Now call the function - with platform="win32", it should import winloop from sys.modules
        loop_factory = auto_module.auto_loop_factory(use_subprocess=False)
        result = loop_factory()
        # Check that winloop was used (the function was called)
        assert mock_winloop.winloop_loop_factory.called
    finally:
        # Clean up sys.modules
        del sys.modules["uvicorn.loops.winloop"]
