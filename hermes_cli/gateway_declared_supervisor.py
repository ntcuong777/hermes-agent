"""A gateway service that Hermes did not install, declared in ``config.yaml``.

A launchd agent / systemd unit managed outside Hermes (a Nix-managed one, say) runs the gateway
``--external-supervisor``. Setup only recognises its own ``ai.hermes.gateway`` plist and
``hermes-gateway.service`` unit, so it offers to install a service, and the second supervisor then
races the first for one gateway: the loser exits "already serves" and is respawned forever.

``gateway.external_supervisor`` in config.yaml is the declaration that something else owns the
service. A label string names it, ``true`` leaves it unnamed. It is read from config, not from the
environment, so it holds for the user's shell, the gateway and cron alike, and it does not make a
shell look like a supervised gateway to the self-targeting guards (``is_supervised_gateway_launch``).
"""

from __future__ import annotations


def declared_external_supervisor() -> str | None:
    """The service ``gateway.external_supervisor`` says owns the gateway, else None."""
    try:
        from hermes_cli.config import load_config_readonly

        section = load_config_readonly().get("gateway")
    except Exception:  # a config that will not load must not break setup or install
        return None
    value = section.get("external_supervisor") if isinstance(section, dict) else None
    if value is True:
        return "an external supervisor"
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def note_externally_supervised(label: str) -> None:
    """Say why Hermes installs no service, and how to restart the gateway instead."""
    print(
        f"✓ The gateway service is managed by {label} (gateway.external_supervisor in config.yaml)."
    )
    print(
        "  Hermes installs no service of its own beside it: two supervisors race for one gateway."
    )
    print("  Restart it with: hermes gateway restart")


def offer_restart_through_supervisor(label: str) -> None:
    """Setup wizard tail for an externally supervised gateway: no install offer, only a restart."""
    from gateway.status import get_running_pid
    from hermes_cli.cli_output import prompt_yes_no
    from hermes_cli.gateway_supervised_restart import (
        gateway_declares_external_supervisor,
        restart_externally_supervised_gateway,
    )

    note_externally_supervised(label)
    pid = get_running_pid()
    if not (pid and gateway_declares_external_supervisor(pid)):
        print("  The gateway is not running: start it through that supervisor.")
        return
    if prompt_yes_no("  Restart the gateway to pick up changes?", True):
        try:
            restart_externally_supervised_gateway(pid)
        except SystemExit:
            print(
                "  Restart failed: check the supervisor, then run: hermes gateway restart"
            )
