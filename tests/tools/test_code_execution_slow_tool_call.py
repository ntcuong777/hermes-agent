"""A tool call parked on a human (approval) longer than the stub's old 300 s recv timeout must be
delivered once and dispatched once: the stub may only retry on connect/send failure, never after
the request went out, and its wait must cover the host's human-wait ceiling.

Wall-clock is compressed 1000x in the stub namespace (socket timeouts and ``time.monotonic``) so
"300 s" is 0.3 s here."""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

from tools.code_execution_tool import generate_hermes_tools_module

_SCALE = 1000.0
_TOKEN = "slow-call-token"
_DELAY_S = 0.8  # far past the scaled 300 s (0.3 s), well inside the scaled ceiling (>= 1.8 s)


class _ScaledSocket:
    """Socket proxy: finite timeouts shrink by _SCALE; everything else passes through."""

    def __init__(self, sock):
        self._sock = sock

    def settimeout(self, value):
        self._sock.settimeout(None if value is None else value / _SCALE)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def _scaled_socket_module():
    shim = types.ModuleType("socket")
    shim.__dict__.update(socket.__dict__)
    shim.socket = lambda *a, **k: _ScaledSocket(socket.socket(*a, **k))
    return shim


def _scaled_time_module():
    shim = types.ModuleType("time")
    shim.__dict__.update(time.__dict__)
    shim.monotonic = lambda: time.monotonic() * _SCALE
    return shim


class SlowToolCallDeliveredOnceTest(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "UDS not available on Windows")
    def test_uds_slow_call_delivered_and_dispatched_once(self):
        from tools.code_execution_rpc import _rpc_server_loop

        dispatched = []

        def slow_dispatch(tool_name, tool_args):
            dispatched.append(tool_name)
            time.sleep(_DELAY_S)
            return json.dumps({"output": f"done {len(dispatched)}"})

        with tempfile.TemporaryDirectory(prefix="h", dir="/tmp") as tmp:
            path = os.path.join(tmp, "s")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(4)
            stop = threading.Event()

            def serve():  # the kernel's _rpc_forever: re-accept until teardown
                while not stop.is_set():
                    _rpc_server_loop(server, "", [], [0], 10, frozenset({"terminal"}), stop, _TOKEN,
                                     dispatch=slow_dispatch)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            namespace = {"__name__": "hermes_tools"}
            try:
                with patch.dict(os.environ, {"HERMES_RPC_SOCKET": path, "HERMES_RPC_TOKEN": _TOKEN,
                                             "HERMES_RPC_PERSISTENT": "1"}):
                    exec(generate_hermes_tools_module(["terminal"], transport="uds"), namespace)
                    namespace["socket"] = _scaled_socket_module()
                    result = namespace["terminal"]("echo hi")
                    time.sleep(_DELAY_S + 0.3)  # a (buggy) retry would dispatch again by now
            finally:
                stop.set()
                sock = namespace.get("_sock")
                if sock is not None:
                    sock.close()
                server.close()
                thread.join(timeout=5)

        self.assertEqual(result, {"output": "done 1"})
        self.assertEqual(dispatched, ["terminal"])

    @unittest.skipIf(sys.platform == "win32", "UDS not available on Windows")
    def test_uds_stale_connection_is_reconnected_before_send(self):
        """The legitimate retry case (server closed an idle connection between cells) still works
        and dispatches exactly once."""
        from tools.code_execution_rpc import _rpc_server_loop

        dispatched = []

        def dispatch(tool_name, tool_args):
            dispatched.append(tool_name)
            return json.dumps({"output": "ok"})

        with tempfile.TemporaryDirectory(prefix="h", dir="/tmp") as tmp:
            path = os.path.join(tmp, "s")
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(path)
            server.listen(4)
            stop = threading.Event()
            accepted = []

            def serve():
                while not stop.is_set():
                    _rpc_server_loop(server, "", [], [0], 10, frozenset({"terminal"}), stop, _TOKEN,
                                     dispatch=dispatch)
                    accepted.append(1)

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            namespace = {"__name__": "hermes_tools"}
            try:
                with patch.dict(os.environ, {"HERMES_RPC_SOCKET": path, "HERMES_RPC_TOKEN": _TOKEN,
                                             "HERMES_RPC_PERSISTENT": "1"}):
                    exec(generate_hermes_tools_module(["terminal"], transport="uds"), namespace)
                    first = namespace["terminal"]("one")
                    namespace["_sock"].shutdown(socket.SHUT_RDWR)  # peer sees EOF: serving loop returns
                    deadline = time.monotonic() + 3
                    while not accepted and time.monotonic() < deadline:
                        time.sleep(0.01)
                    # Stale-peer simulation: the server side closed, the stub still holds the socket.
                    namespace["_sock"] = _ClosedPeer(namespace["_sock"])
                    second = namespace["terminal"]("two")
            finally:
                stop.set()
                server.close()
                thread.join(timeout=5)

        self.assertEqual((first, second), ({"output": "ok"}, {"output": "ok"}))
        self.assertEqual(dispatched, ["terminal", "terminal"])

    def test_file_rpc_waits_past_the_old_300s_deadline(self):
        from tools.code_execution_tool import _rpc_poll_loop
        from tests.tools.test_code_execution_rpc import _FakeFileRpcEnvironment

        dispatched = []

        def slow_call(function_name, function_args, task_id=None, user_task=None, **_kw):
            dispatched.append(function_name)
            time.sleep(_DELAY_S)
            return json.dumps({"output": "done"})

        stop = threading.Event()
        with tempfile.TemporaryDirectory(prefix="hermes-rpc-") as tmp:
            rpc_dir = os.path.join(tmp, "rpc")
            os.mkdir(rpc_dir)
            env = _FakeFileRpcEnvironment(rpc_dir)

            def poll():
                with patch("model_tools.handle_function_call", side_effect=slow_call):
                    _rpc_poll_loop(env, rpc_dir, "t", [], [0], 10, frozenset({"terminal"}), stop, _TOKEN)

            thread = threading.Thread(target=poll, daemon=True)
            thread.start()
            namespace = {"__name__": "hermes_tools"}
            try:
                with patch.dict(os.environ, {"HERMES_RPC_DIR": rpc_dir, "HERMES_RPC_TOKEN": _TOKEN}):
                    exec(generate_hermes_tools_module(["terminal"], transport="file"), namespace)
                    namespace["time"] = _scaled_time_module()
                    result = namespace["terminal"]("echo hi")
            finally:
                stop.set()
                thread.join(timeout=5)

        self.assertEqual(result, {"output": "done"})
        self.assertEqual(dispatched, ["terminal"])


class _ClosedPeer:
    """Socket whose peer is gone: reads EOF, writes fail (like a server-closed idle UDS)."""

    def __init__(self, sock):
        self._sock = sock

    def recv(self, *a, **k):
        return b""

    def sendall(self, *a, **k):
        raise BrokenPipeError("peer closed")

    def close(self):
        self._sock.close()

    def fileno(self):
        return self._sock.fileno()

    def __getattr__(self, name):
        return getattr(self._sock, name)


if __name__ == "__main__":
    unittest.main()
