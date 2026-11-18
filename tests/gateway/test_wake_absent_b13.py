"""Stage B13 (spec 08b section 9.4): the wake of a moved session is upstream's again when no plugin runs.

The lane-mirror plugin (feature id `wake`) carries the origin wake and the notice retarget. This core no longer has
that code: the automation and notification files are upstream's text, byte for byte, and the plugin's three patches
are the only way a moved session's background result or watch notice is delivered again.
"""

import inspect

import gateway.run_notifications as run_notifications
import gateway.session_automation as session_automation
import gateway.session_ingress_context as ingress_context
import gateway.session_local_recovery as local_recovery


def test_the_automation_module_has_no_origin_wake_code():
    for name in ("origin_wake_source", "_snapshot_origin_wake", "_shared_route", "_wake_payload",
                 "_route_inputs", "_route_is_locally_driven", "_LOCAL_TURN_FIELDS"):
        assert not hasattr(session_automation, name), name


def test_snapshot_automation_still_refuses_a_route_without_a_committed_envelope():
    source = inspect.getsource(session_automation.snapshot_automation)
    assert "envelope = wake_route_envelope(authority, entry, event.metadata.get('requester_user_id'))" in source
    assert "if envelope is None:\n        raise RuntimeStoreError('not_found')" in source
    assert "_snapshot_origin_wake" not in source


def test_the_notice_drain_does_not_retarget_a_frozen_event_in_core():
    assert not hasattr(local_recovery, "retarget_handed_off_event")
    assert not hasattr(local_recovery, "_FROZEN_ROUTE_FIELDS")
    assert "retarget_handed_off_event" not in inspect.getsource(run_notifications)


def test_the_ingress_context_has_no_route_provenance_in_core():
    assert not hasattr(ingress_context, "route_provenance")
