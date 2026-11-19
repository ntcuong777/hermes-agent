"""Seams for a gateway service Hermes did not install.

runtime-custom reads ``gateway.external_supervisor``. Absent, nothing is declared
and install proceeds.
"""

from __future__ import annotations


def declared_external_supervisor() -> str | None:
    return None


def stop_if_declared() -> bool:
    return False


def note_externally_supervised(label: str) -> None:
    del label


def offer_restart_through_supervisor(label: str) -> None:
    del label
