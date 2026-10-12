"""``gateway.external_supervisor`` declares a gateway service Hermes did not install.

A launchd agent / systemd unit managed outside Hermes (nix-darwin, say) runs ``gateway run
--external-supervisor``. Setup only recognises its own ``ai.hermes.gateway`` plist, so it offered to
install a service and the two supervisors then raced for one gateway. The config key is the
declaration that something else owns the service; with it, Hermes installs nothing of its own.
"""

from __future__ import annotations

import pytest

LABEL = "org.example.hermes-gateway"


class _Reached(Exception):
    """Raised by a stub that must only run when the install proceeds."""


def _boom(*_a, **_k):
    raise AssertionError("an install step ran although a supervisor is declared")


@pytest.fixture
def declare(tmp_path, monkeypatch):
    """Point HERMES_HOME at a tmp dir and let a test write its config.yaml."""
    import hermes_constants

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes_constants, "_default_hermes_root_memo", None)

    def write(text: str) -> None:
        (tmp_path / "config.yaml").write_text(text)

    return write


@pytest.mark.parametrize(
    "config, expected",
    [
        ("model: {default: x}\n", None),
        ("gateway: {multiplex_profiles: true}\n", None),
        (f"gateway: {{external_supervisor: {LABEL}}}\n", LABEL),
        ("gateway: {external_supervisor: true}\n", "an external supervisor"),
        ("gateway: {external_supervisor: false}\n", None),
        ('gateway: {external_supervisor: ""}\n', None),
        ("gateway: 3\n", None),
    ],
)
def test_declaration_is_read_from_config(declare, config, expected):
    from hermes_cli.gateway_declared_supervisor import declared_external_supervisor

    declare(config)
    assert declared_external_supervisor() == expected


def test_a_config_that_will_not_load_declares_nothing(declare, monkeypatch):
    import hermes_cli.config as cfg
    from hermes_cli.gateway_declared_supervisor import declared_external_supervisor

    monkeypatch.setattr(cfg, "load_config_readonly", _boom)
    assert declared_external_supervisor() is None


def test_launchd_install_does_nothing_when_a_supervisor_is_declared(
    declare, monkeypatch, capsys
):
    import hermes_cli.gateway as gw
    import hermes_cli.gateway_launchd as gl

    declare(f"gateway: {{external_supervisor: {LABEL}}}\n")
    monkeypatch.setattr(gw, "get_launchd_plist_path", _boom)
    gl.launchd_install()
    out = capsys.readouterr().out
    assert LABEL in out and "hermes gateway restart" in out


def test_launchd_install_still_installs_when_nothing_is_declared(declare, monkeypatch):
    import hermes_cli.gateway as gw
    import hermes_cli.gateway_launchd as gl

    declare("model: {default: x}\n")

    def reached(*_a, **_k):
        raise _Reached

    monkeypatch.setattr(gw, "get_launchd_plist_path", reached)
    with pytest.raises(_Reached):
        gl.launchd_install()


def test_systemd_install_does_nothing_when_a_supervisor_is_declared(
    declare, monkeypatch, capsys
):
    import hermes_cli.gateway as gw

    declare(f"gateway: {{external_supervisor: {LABEL}}}\n")
    monkeypatch.setattr(gw, "get_systemd_unit_path", _boom)
    monkeypatch.setattr(gw, "_require_root_for_system_service", _boom)
    gw.systemd_install(force=False)
    assert LABEL in capsys.readouterr().out


def test_systemd_install_still_installs_when_nothing_is_declared(declare, monkeypatch):
    import hermes_cli.gateway as gw

    declare("model: {default: x}\n")

    def reached(*_a, **_k):
        raise _Reached

    monkeypatch.setattr(gw, "get_systemd_unit_path", reached)
    with pytest.raises(_Reached):
        gw.systemd_install(force=False)


def test_setup_flows_need_no_service_when_a_supervisor_is_declared(
    declare, monkeypatch, capsys
):
    """``hermes setup`` / ``import`` / the wizard all stop at this one check."""
    import hermes_cli.gateway as gw

    declare(f"gateway: {{external_supervisor: {LABEL}}}\n")
    monkeypatch.setattr(gw, "named_profile_served_by_running_multiplexer", _boom)
    assert gw._served_profile_needs_no_service() is True
    assert LABEL in capsys.readouterr().out


@pytest.fixture
def wizard(declare, monkeypatch):
    """A declared supervisor, a running gateway that is externally supervised, and a spy on restarts."""
    import gateway.status
    import hermes_cli.cli_output as cli_output
    import hermes_cli.gateway as gw
    import hermes_cli.gateway_supervised_restart as sup

    declare(f"gateway: {{external_supervisor: {LABEL}}}\n")
    state = {"restarts": [], "answer": True, "pid": 4242, "supervised": True}
    monkeypatch.setattr(gateway.status, "get_running_pid", lambda *a, **k: state["pid"])
    monkeypatch.setattr(
        sup,
        "gateway_declares_external_supervisor",
        lambda pid, home=None: state["supervised"],
    )
    monkeypatch.setattr(
        sup,
        "restart_externally_supervised_gateway",
        lambda pid: state["restarts"].append(pid),
    )
    monkeypatch.setattr(
        cli_output, "prompt_yes_no", lambda q, default=True: state["answer"]
    )
    # Nothing that installs or inspects a Hermes-owned service may run.
    for name in (
        "_is_service_installed",
        "_is_service_running",
        "_wizard_install_service",
    ):
        monkeypatch.setattr(gw, name, _boom)
    return state


def test_wizard_offers_a_restart_through_the_supervisor_not_an_install(wizard, capsys):
    import hermes_cli.gateway_setup_wizard as wiz

    wiz._wizard_post_setup()
    assert wizard["restarts"] == [4242]
    assert LABEL in capsys.readouterr().out


def test_wizard_restart_can_be_declined(wizard):
    import hermes_cli.gateway_setup_wizard as wiz

    wizard["answer"] = False
    wiz._wizard_post_setup()
    assert wizard["restarts"] == []


def test_wizard_with_no_running_gateway_points_at_the_supervisor(wizard, capsys):
    import hermes_cli.gateway_setup_wizard as wiz

    wizard["pid"] = None
    wiz._wizard_post_setup()
    assert wizard["restarts"] == []
    assert "start it through that supervisor" in capsys.readouterr().out


def test_wizard_survives_a_failed_restart(wizard, monkeypatch, capsys):
    import hermes_cli.gateway_setup_wizard as wiz
    import hermes_cli.gateway_supervised_restart as sup

    def fail(pid):
        raise SystemExit(1)

    monkeypatch.setattr(sup, "restart_externally_supervised_gateway", fail)
    wiz._wizard_post_setup()
    assert "Restart failed" in capsys.readouterr().out


def test_wizard_status_block_names_the_supervisor_and_skips_the_service_probes(
    wizard, capsys
):
    import hermes_cli.gateway_setup_wizard as wiz

    wiz._wizard_service_status_block()
    assert LABEL in capsys.readouterr().out
