"""hassfest-like manifest validation."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "glowcast" / "manifest.json"
HACS = ROOT / "hacs.json"


def test_manifest_required_keys():
    data = json.loads(MANIFEST.read_text())
    assert data["domain"] == "glowcast"
    assert data["name"] == "GlowCast"
    assert data["config_flow"] is True
    assert data["version"]
    assert data["iot_class"] in {
        "calculated",
        "local_push",
        "local_polling",
        "cloud_polling",
        "cloud_push",
    }
    assert isinstance(data.get("requirements"), list)


def test_hacs_json():
    data = json.loads(HACS.read_text())
    assert data["name"] == "GlowCast"


def test_no_yaml_config_required():
    init = (ROOT / "custom_components" / "glowcast" / "__init__.py").read_text()
    assert "config_entry_only_config_schema" in init


def test_domain_folder_matches():
    assert (ROOT / "custom_components" / "glowcast").is_dir()
    assert not (ROOT / "custom_components" / "lumasync").exists()
