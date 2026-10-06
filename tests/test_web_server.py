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
            assert "no-store" in resp.headers.get("Cache-Control", "")
            content = resp.read().decode("utf-8")
            assert "JARVIS" in content
            assert "speaking-orb" in content
            assert "particle-field" in content
            assert "orb-hud" in content

        # Verify CSS and JS assets resolve directly
        css_url = f"{server.url}/css/main.css"
        with urllib.request.urlopen(urllib.request.Request(css_url), timeout=2.0) as resp_css:
            assert resp_css.status == 200
            assert "text/css" in resp_css.headers.get("Content-Type", "")

        js_url = f"{server.url}/js/app.js"
        with urllib.request.urlopen(urllib.request.Request(js_url), timeout=2.0) as resp_js:
            assert resp_js.status == 200
            assert "javascript" in resp_js.headers.get("Content-Type", "")

        # Verify /frontend/index.html route alias works transparently
        url_alias = f"{server.url}/frontend/index.html"
        req_alias = urllib.request.Request(url_alias)
        with urllib.request.urlopen(req_alias, timeout=2.0) as resp_alias:
            assert resp_alias.status == 200
            content_alias = resp_alias.read().decode("utf-8")
            assert "JARVIS" in content_alias
            assert "particle-field" in content_alias

        # Verify root path / serves index.html
        url_root = f"{server.url}/"
        req_root = urllib.request.Request(url_root)
        with urllib.request.urlopen(req_root, timeout=2.0) as resp_root:
            assert resp_root.status == 200
            content_root = resp_root.read().decode("utf-8")
            assert "JARVIS" in content_root
            assert "particle-field" in content_root
    finally:
        server.stop()
        assert not server.is_running
