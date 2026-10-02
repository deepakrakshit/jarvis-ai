"""Test Suite for JARVIS Static Web Server."""

import socket
import urllib.request

from jarvis.gateway.web_server import JarvisWebServer


def get_free_port() -> int:
    """Dynamically allocate an available ephemeral TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def test_web_server_lifecycle() -> None:
    """Verify web server starts, serves static files, and stops cleanly."""
    port = get_free_port()
    server = JarvisWebServer(host="127.0.0.1", port=port)

    try:
        active_port = server.start(open_browser=False)
        assert active_port == port
        assert server.is_running

        # Verify HTTP response from index.html
        url = f"{server.url}/index.html"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            assert resp.status == 200
            content = resp.read().decode("utf-8")
            assert "JARVIS" in content
            assert "speaking-orb" in content
    finally:
        server.stop()
        assert not server.is_running
