from __future__ import annotations

from hermitcrab.agent.tools.web import _is_unsafe_ip, _validate_url


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
