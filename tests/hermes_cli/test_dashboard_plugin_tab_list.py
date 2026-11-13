"""The dashboard plugin list offers a tab only for a plugin whose bundle the browser can load.

A plugin directory may carry just the backend layer of a dashboard extension
(``dashboard/manifest.json`` + ``plugin_api.py``): the native desktop app shares only the
``/api/plugins/<name>`` namespace with the web dashboard and ships its UI through its own SDK. The
browser turns every listed plugin into a tab and a ``<script>`` tag, so listing one with no bundle
gave a tab that could only say "Could not load this plugin's script".

Driven through the real app on the daemon stand-in of the consent-gate tests.
"""
from __future__ import annotations

import json

from tests.hermes_cli import test_plugin_api_consent_gate as consent_gate

daemon = consent_gate.daemon  # the multiplexed daemon stand-in fixture
_install, _plugins = consent_gate._install, consent_gate._plugins
NAME = "backend-only"


def _tabs(daemon):
    return {p["name"] for p in daemon.client.get("/api/dashboard/plugins", headers=daemon.owner).json()}


def test_plugin_without_a_bundle_keeps_its_api_and_gets_no_tab(daemon):
    bundle = _install(daemon.launch, NAME).with_name("index.js")
    bundle.unlink()
    _plugins(daemon.launch, enabled=[NAME])

    assert daemon.client.get(f"/api/plugins/{NAME}/ping", headers=daemon.owner).status_code == 200
    assert NAME not in _tabs(daemon)

    bundle.write_text("export {}\n")  # built later: the tab appears without a rescan
    assert NAME in _tabs(daemon)


def test_bundle_outside_the_dashboard_dir_gets_no_tab(daemon):
    """The asset route refuses a path that leaves ``dashboard/``, so that script cannot load either."""
    dashboard = _install(daemon.launch, NAME).parent
    (dashboard / "manifest.json").write_text(json.dumps({"name": NAME, "entry": "../index.js"}))
    (dashboard.parent / "index.js").write_text("export {}\n")
    _plugins(daemon.launch, enabled=[NAME])

    assert NAME not in _tabs(daemon)
