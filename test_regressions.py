import queue
import socket
import subprocess
import sys
import threading
import unittest

from pathlib import Path
from unittest.mock import Mock, patch

from ifcbclient import IFCBClient, connection
from ifcbclient.ifcbclient import _Connection
from signalrcore.messages.completion_message import CompletionMessage
from signalrcore.messages.invocation_message import InvocationMessage
from signalrcore.protocol.json_hub_protocol import JsonHubProtocol
from signalrcore.transport.websockets.websocket_client import WebSocketClient
from signalrcore.transport.websockets.websocket_transport import (
    WebsocketTransport,
)


# Exercise the public client through real SignalR dispatch and transport code
class TestClientRegressions(unittest.TestCase):
    def setUp(self):
        self.opened = queue.Queue()
        self.clients = []
        self.socket_sends = {}
        self.sockets = {}
        self.use_socket = False
        self.start_patch = patch.object(
            _Connection, 'connect', autospec=True,
            side_effect=self.open_connection,
        )
        self.connect_mock = self.start_patch.start()
        self.addCleanup(self.start_patch.stop)
        self.addCleanup(self.stop_clients)

    def stop_clients(self):
        for client in self.clients:
            client.stop()

    def make_client(self, client_class=IFCBClient, **kwargs):
        client = client_class(
            'ws://localhost:8092/ifcbHub', '111-111-152',
            auto_reconnect=False, **kwargs,
        )
        self.clients.append(client)
        return client

    def open_connection(self, attempt):
        hub = attempt.hub_connection
        transport = WebsocketTransport(
            url=hub.url,
            protocol=JsonHubProtocol(),
            on_open=hub._callbacks.on_open,
            on_close=hub._callbacks.on_close,
            on_message=hub.on_message,
        )

        # Retain actual socket writes when exercising cancellation
        if self.use_socket:
            local, peer = socket.socketpair()
            self.sockets[hub] = (local, peer)
            transport._client = WebSocketClient(hub.url)
            transport._client.sock = local
            transport._client.running = True

            def receive():
                try:
                    local.recv(1)
                except OSError:
                    pass  # Cleanup may close the socket first

            receiver = threading.Thread(
                target=receive, name=transport._client.thread_name, daemon=True
            )
            transport._client.recv_thread = receiver
            receiver.start()
            self.addCleanup(receiver.join, 2)
            self.addCleanup(local.close)
            self.addCleanup(peer.close)
        else:
            transport._client = Mock(spec=['send', 'sock'])
            transport._client.sock = None
        self.socket_sends[hub] = transport._client.send

        # Keep the watchdog under test separate from outbound keepalive
        transport.connection_checker = Mock()
        hub.transport = transport
        transport.state = type(transport.state).connecting
        transport.on_message(None, '{}\x1e')
        self.opened.put(hub)

    def register(self, client):
        ready = threading.Event()
        client.on_ready(lambda value: ready.set() if value else None)
        client.connect(background=True)
        hub = self.opened.get(timeout=2)
        self.emit(hub, 'startedAsClient', [])
        self.assertTrue(ready.wait(2), 'client did not become ready')
        return hub

    def emit(self, hub, target, arguments):
        hub.on_message([InvocationMessage(
            invocation_id=None, target=target, arguments=arguments,
        )])

    def run_in_thread(self, callback):
        finished = threading.Event()
        result = []

        def run():
            try:
                result.append(callback())
            except Exception as error:
                result.append(error)
            finally:
                finished.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, finished, result

    def test_default_connect_raises_initial_transport_failure(self):
        failure = OSError('initial connection refused')
        self.connect_mock.side_effect = failure
        client = self.make_client()
        with self.assertRaises(OSError) as raised:
            client.connect()
        self.assertIs(raised.exception, failure)
        self.connect_mock.assert_called_once()

    def test_send_preserves_exception_contract_after_synchronous_close(self):
        client = self.make_client()
        hub = self.register(client)
        errors = []
        reported = threading.Event()

        def on_error(error):
            errors.append(error)
            reported.set()

        client.on_error(on_error)
        self.socket_sends[hub].side_effect = OSError('broken socket')

        # SignalR reports closure before raising its transport send exception
        with self.assertRaises(ValueError) as raised:
            client.relay_message_to_host('sample', timeout=0)
        self.assertTrue(reported.wait(2), 'send failure was not reported')
        self.assertEqual(errors, [raised.exception])
        self.assertTrue(hub.transport.is_disconnected())

    def test_invocation_error_does_not_disconnect_healthy_transport(self):
        client = self.make_client()
        ready = []
        client.on_ready(ready.append)
        hub = self.register(client)
        errors = []
        reported = threading.Event()
        closed = Mock()
        client.on_close(closed)

        def on_error(error):
            errors.append(error)
            reported.set()

        client.on_error(on_error)
        failure = CompletionMessage(
            invocation_id='123', result=None, error='Command rejected',
        )
        hub.on_message([failure])
        self.assertTrue(reported.wait(2))
        self.assertEqual(errors, [failure])
        self.assertEqual(ready, [False, True])
        self.assertTrue(hub.transport.is_connected())
        self.assertIsNone(client.relay_message_to_host('refresh', timeout=0))
        closed.assert_not_called()

    def test_callback_can_wait_for_another_thread_to_send(self):
        client = self.make_client()
        hub = self.register(client)
        callback_done = threading.Event()
        completed_in_callback = []
        send_results = []

        def callback(_):
            thread, finished, result = self.run_in_thread(
                lambda: client.relay_message_to_host('refresh', timeout=0)
            )
            completed_in_callback.append(finished.wait(1))
            send_results.extend(result)
            callback_done.set()

        client.on_any_message(callback)
        self.emit(hub, 'messageRelayed', [0, 0, 0, 'syringetrack:1.5'])
        self.assertTrue(callback_done.wait(2))
        self.assertEqual(completed_in_callback, [True])
        self.assertEqual(send_results, [None])

    def test_callback_can_wait_for_another_thread_to_stop(self):
        client = self.make_client()
        hub = self.register(client)
        callback_done = threading.Event()
        completed_in_callback = []

        def callback(_):
            thread, finished, result = self.run_in_thread(client.stop)
            completed_in_callback.append(finished.wait(1))
            callback_done.set()

        client.on_any_message(callback)
        self.emit(hub, 'messageRelayed', [0, 0, 0, 'syringetrack:1.5'])
        self.assertTrue(callback_done.wait(2))
        self.assertEqual(completed_in_callback, [True])

    def test_subclass_connection_and_registration_hooks_still_run(self):
        calls = []
        heartbeat = threading.Event()

        class CustomClient(IFCBClient):
            def on_connect(self):
                calls.append('open')
                super().on_connect()

            def started(self, response):
                calls.append(('started', response))
                super().started(response)

            def do_nothing(self, response):
                calls.append(('heartbeat', response))
                super().do_nothing(response)
                heartbeat.set()

        client = self.make_client(client_class=CustomClient)
        hub = self.register(client)
        self.emit(hub, 'heartbeat', [123])
        self.assertTrue(heartbeat.wait(2))
        self.assertEqual(calls, [
            'open', ('started', []), ('heartbeat', [123]),
        ])

    def blocked_send(self, client, hub):
        local, peer = self.sockets[hub]
        local.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024)
        socket_client = hub.transport._client
        entered = threading.Event()
        original_send = socket_client.send

        def send(*args, **kwargs):
            entered.set()
            return original_send(*args, **kwargs)

        socket_client.send = send
        sender, finished, result = self.run_in_thread(
            lambda: client.relay_message_to_host('x' * 1000000, timeout=0)
        )
        self.assertTrue(entered.wait(2))
        self.assertFalse(finished.wait(0.1), 'write did not block')
        return local, peer, sender, finished, result

    def test_stop_interrupts_blocked_socket_write_without_peer_help(self):
        self.use_socket = True
        client = self.make_client()
        hub = self.register(client)
        local, peer, sender, finished, result = self.blocked_send(client, hub)
        closer, stopped, stop_result = self.run_in_thread(client.stop)
        try:
            self.assertTrue(
                stopped.wait(2), 'stop waited for the blocked send'
            )
            self.assertTrue(finished.wait(2), 'socket write stayed blocked')
            self.assertIsInstance(result[0], Exception)
            self.assertEqual(stop_result, [None])
            self.assertEqual(local.fileno(), -1)
            self.assertNotEqual(peer.fileno(), -1)
        finally:
            peer.close()  # Release a broken implementation too
            sender.join(timeout=2)
            closer.join(timeout=2)

    def test_heartbeat_expiry_interrupts_blocked_socket_write(self):
        self.use_socket = True
        client = self.make_client()
        ready = []
        errors = []
        reported = threading.Event()

        def on_error(error):
            errors.append(error)
            reported.set()

        client.on_ready(ready.append)
        client.on_error(on_error)
        now = [100.0]
        client._connection.clock = lambda: now[0]
        hub = self.register(client)
        local, peer, sender, finished, result = self.blocked_send(client, hub)
        try:
            condition = client._connection.condition
            self.assertTrue(condition.acquire(timeout=1))
            try:
                now[0] += connection.HEARTBEAT_TIMEOUT
                condition.notify_all()
            finally:
                condition.release()
            self.assertTrue(
                finished.wait(2), 'watchdog could not stop the send'
            )
            self.assertIsInstance(result[0], Exception)
            client._connection.worker.join(timeout=2)
            self.assertFalse(client._connection.worker.is_alive())
            self.connect_mock.assert_called_once()
            self.assertTrue(reported.wait(2))
            self.assertEqual(ready, [False, True, False])
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], TimeoutError)
            self.assertEqual(local.fileno(), -1)
            self.assertNotEqual(peer.fileno(), -1)
        finally:
            peer.close()  # Release a broken implementation too
            sender.join(timeout=2)


class TestCommandLineFailure(unittest.TestCase):
    def test_refused_connection_is_reported_and_exits(self):

        # Reserve a local port without listening, so connection must fail
        with socket.socket() as reserved:
            reserved.bind(('127.0.0.1', 0))
            url = 'ws://127.0.0.1:%s/ifcbHub' % reserved.getsockname()[1]
            result = subprocess.run(
                [sys.executable, '-m', 'ifcbclient', url, '111-111-152'],
                cwd=Path(__file__).resolve().parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=5,
            )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Connection refused', result.stderr)
