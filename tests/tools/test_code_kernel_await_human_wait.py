"""A cell's time budget must not run while one of its tool calls waits on a human approval.

Logged symptom: an execute_code cell whose tool call raised an approval prompt was killed with
"Cell timed out after 300s" while the prompt was still open (the approval wait ended unanswered
afterwards), which the model and the user read as the tool timing out.
"""

import queue
import threading
import time
from types import SimpleNamespace

from tools import code_kernel
from tools.approval_human_wait import human_wait_window


def _kernel(reply_after_s=None):
    q = queue.Queue()
    if reply_after_s is not None:
        threading.Timer(reply_after_s, lambda: q.put({"status": "ok"})).start()
    return SimpleNamespace(response_q=q)


def test_cell_without_a_human_wait_times_out_at_its_budget():
    start = time.monotonic()
    status, _ = code_kernel._await_cell(_kernel(), 0.3, lambda: False)
    assert status == "timeout"
    assert time.monotonic() - start < 1.5


def test_cell_parked_on_an_approval_outlives_its_budget_and_gets_the_late_reply():
    kernel = _kernel(reply_after_s=1.2)

    def approval_wait():
        with human_wait_window("kernel-await-session"):
            time.sleep(1.0)

    from tools import approval_context
    original = approval_context.get_current_session_key
    approval_context.get_current_session_key = lambda *a, **k: "kernel-await-session"
    try:
        threading.Thread(target=approval_wait).start()
        status, payload = code_kernel._await_cell(kernel, 0.3, lambda: False)
    finally:
        approval_context.get_current_session_key = original
    assert (status, payload) == ("success", {"status": "ok"})
