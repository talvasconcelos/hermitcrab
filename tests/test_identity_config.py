"""Focused regressions for beta4 identity config paths."""

from __future__ import annotations

import pytest

from hermitcrab.config.schema import Config


def test_default_identity_paths_use_beta4_layout() -> None:
    config = Config()

    assert config.hermitcrab_root_path.as_posix().endswith("/.hermitcrab")
    assert config.system_root_path.as_posix().endswith("/.hermitcrab/system")
    assert config.identities_root_path.as_posix().endswith("/.hermitcrab/identities")
    assert config.owner_identity_name == "owner"
    assert config.owner_identity_root_path.as_posix().endswith("/.hermitcrab/identities/owner")


def test_identity_paths_resolve_under_configured_root(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})

    assert config.hermitcrab_root_path == tmp_path
    assert config.system_root_path == tmp_path / "system"
    assert config.identities_root_path == tmp_path / "identities"
    assert config.get_identity_path("alice") == tmp_path / "identities" / "alice"


def test_identity_paths_accept_configured_relative_and_absolute_roots(tmp_path) -> None:
    external = tmp_path / "external" / "client_acme"
    config = Config.model_validate(
        {
            "root": str(tmp_path / "hc"),
            "identities": {
                "root": "people",
                "ownerIdentity": "tal",
                "registry": {
                    "tal": {"root": "owner"},
                    "client_acme": {"root": str(external)},
                },
            },
            "system": {"root": "runtime"},
        }
    )

    assert config.system_root_path == tmp_path / "hc" / "runtime"
    assert config.identities_root_path == tmp_path / "hc" / "people"
    assert config.owner_identity_root_path == tmp_path / "hc" / "people" / "owner"
    assert config.get_identity_path("client_acme") == external
    assert config.configured_identities() == {
        "tal": tmp_path / "hc" / "people" / "owner",
        "client_acme": external,
    }


@pytest.mark.parametrize("name", ["", " shared ", "system", "../alice", "bad/name", "-bad"])
def test_identity_config_rejects_reserved_or_unsafe_names(name: str) -> None:
    with pytest.raises(ValueError):
        Config.model_validate({"identities": {"ownerIdentity": name}})


def test_identity_registry_rejects_reserved_or_unsafe_names() -> None:
    with pytest.raises(ValueError):
        Config.model_validate(
            {
                "identities": {
                    "registry": {
                        "shared": {"root": "shared"},
                    }
                }
            }
        )


def test_legacy_workspace_layout_detection(tmp_path) -> None:
    config = Config.model_validate({"root": str(tmp_path)})
    assert config.detects_legacy_workspace_layout() is False

    (tmp_path / "workspace").mkdir()
    assert config.detects_legacy_workspace_layout() is True
