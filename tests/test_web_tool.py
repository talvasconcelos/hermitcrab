from __future__ import annotations

from hermitcrab.agent.tools.web import _is_unsafe_ip, _pin_url, _validate_url


def test_ssrf_blocks_loopback_and_private_ips() -> None:
    for url in (
        "http://127.0.0.1/admin",
        "http://localhost/admin",
        "http://[::1]/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",
    ):
        ok, _ = _validate_url(url)
        assert ok is False, url


def test_ssrf_allows_public_ips_and_schemes() -> None:
    assert _validate_url("https://8.8.8.8/")[0] is True
    assert _validate_url("https://example.com/")[0] is True
    assert _validate_url("ftp://example.com/")[0] is False
    assert _validate_url("file:///etc/passwd")[0] is False


def test_is_unsafe_ip_handles_ipv4_mapped_ipv6() -> None:
    assert _is_unsafe_ip("::ffff:127.0.0.1") is True
    assert _is_unsafe_ip("::ffff:169.254.169.254") is True
    assert _is_unsafe_ip("8.8.8.8") is False


def test_pin_url_resolves_once_and_pins_to_validated_ip(monkeypatch) -> None:
    calls: list[str] = []

    def fake_getaddrinfo(host, port):
        calls.append(host)
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("hermitcrab.agent.tools.web.socket.getaddrinfo", fake_getaddrinfo)

    pinned, host, err = _pin_url("https://example.com/path?q=1")

    assert err == ""
    assert host == "example.com"
    assert pinned == "https://93.184.216.34/path?q=1"
    # Resolved exactly once — the address validated is the address pinned.
    assert calls == ["example.com"]


def test_pin_url_rejects_private_resolution(monkeypatch) -> None:
    def fake_getaddrinfo(host, port):
        return [(2, 1, 6, "", ("10.0.0.1", 0))]

    monkeypatch.setattr("hermitcrab.agent.tools.web.socket.getaddrinfo", fake_getaddrinfo)

    pinned, host, err = _pin_url("https://evil.example/")

    assert pinned == ""
    assert "private" in err
