"""Check SignalR keepalive and cleanup with real local sockets."""

import json
import socket
import threading
import unittest

from types import SimpleNamespace
from unittest.mock import Mock

from ifcbclient.ifcbclient import _Connection
from signalrcore.messages.ping_message import PingMessage
from signalrcore.protocol.json_hub_protocol import JsonHubProtocol
from signalrcore.transport.websockets.websocket_client import WebSocketClient
from signalrcore.transport.websockets.websocket_transport import (
    WebsocketTransport,
)


class TestClientTransport(unittest.TestCase):
    def make_client(self):
        return _Connection('ws://localhost:8092/ifcbHub', '111-111-152')

    def attach_transport(self, client, socket_client):
        hub = client.hub_connection
        transport = WebsocketTransport(
            url=hub.url,
            on_close=hub._callbacks.on_close,
            reconnection_handler=hub.kwargs.get('reconnection_handler'),
        )
        transport._client = socket_client
        transport.connection_checker = Mock()
        transport.state = type(transport.state).connected
        hub.transport = transport
        return transport

    def test_keepalive_starts_with_signalr_reconnection_disabled(self):
        client = self.make_client()
        hub = client.hub_connection
        self.assertIsNone(hub.kwargs.get('reconnection_handler'))
        ping = threading.Event()
        messages = []

        # Retain SignalR's encoder and checker thread; replace only the writer
        def record(message, opcode):
            self.assertEqual(opcode, 1)
            message = json.loads(message.rstrip('\x1e'))
            messages.append(message)
            if message['type'] == 6:
                ping.set()

        transport = WebsocketTransport(
            url=hub.url,
            protocol=JsonHubProtocol(),
            on_open=client.on_connect,
            keep_alive_interval=0,
        )
        transport._client = SimpleNamespace(send=record)
        transport.connection_checker.sleep = 0
        self.addCleanup(transport.connection_checker.stop)
        hub.transport = transport

        # The real handshake invokes our workaround before any idle ping
        transport.state = type(transport.state).connecting
        transport.on_message(None, '{}\x1e')
        self.assertTrue(ping.wait(1), 'Client keepalive emitted no ping')
        self.assertTrue(any(
            message.get('target') == 'startAsClient'
            and message.get('arguments') == ['111-111-152']
            for message in messages
        ))

    def test_commands_and_keepalive_do_not_write_concurrently(self):
        client = self.make_client()
        entered = threading.Event()
        release = threading.Event()
        ping_started = threading.Event()
        ping_written = threading.Event()
        errors = []

        # Hold one frame in the writer while another thread tries to ping
        def record(message, opcode):
            message = json.loads(message.rstrip('\x1e'))
            if message.get('target') == 'relayMessageToHost':
                entered.set()
                if not release.wait(2):
                    raise TimeoutError('test did not release command writer')
            elif message['type'] == 6:
                ping_written.set()

        transport = WebsocketTransport(
            url=client.hub_connection.url,
            protocol=JsonHubProtocol(),
        )
        transport._client = SimpleNamespace(send=record)
        transport.connection_checker = Mock()
        transport.state = type(transport.state).connected
        client.hub_connection.transport = transport
        client.on_connect()

        def command():
            try:
                client.relay_message_to_host('refresh')
            except Exception as error:
                errors.append(error)

        def ping():
            ping_started.set()
            try:
                transport.send(PingMessage())
            except Exception as error:
                errors.append(error)

        sender = threading.Thread(target=command, daemon=True)
        keeper = threading.Thread(target=ping, daemon=True)
        sender.start()
        try:
            self.assertTrue(entered.wait(1))
            keeper.start()
            self.assertTrue(ping_started.wait(1))
            self.assertFalse(ping_written.wait(0.1))
        finally:
            release.set()
            sender.join(timeout=1)
            if keeper.ident is not None:
                keeper.join(timeout=1)

        self.assertFalse(sender.is_alive())
        self.assertFalse(keeper.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(ping_written.is_set())

    def test_stop_before_connect_is_harmless(self):
        client = self.make_client()
        self.assertIsNone(client.hub_connection.transport)
        client.stop()
        client.stop()
        self.assertIsNone(client.hub_connection.transport)

    def test_late_open_cannot_restart_keepalive_after_stop(self):
        client = self.make_client()
        transport = self.attach_transport(client, Mock())
        client.hub_connection.send = Mock()
        client.stop()

        # An already admitted open callback may run after attempt retirement
        try:
            client.on_connect()
        except ConnectionError:
            pass
        transport.connection_checker.start.assert_not_called()
        client.hub_connection.send.assert_not_called()

    def test_stop_waits_for_keepalive_start_before_cleaning_it_up(self):
        client = self.make_client()
        transport = self.attach_transport(client, Mock())
        client.hub_connection.send = Mock()
        checker = transport.connection_checker
        checker.running = False
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()
        errors = []

        # Pause resource startup while another thread requests cleanup
        def start_checker():
            entered.set()
            if not release.wait(2):
                raise TimeoutError('test did not release keepalive startup')
            checker.running = True

        def stop_checker():
            checker.running = False
            stopped.set()

        def opened():
            try:
                client.on_connect()
            except Exception as error:
                errors.append(error)

        checker.start.side_effect = start_checker
        checker.stop.side_effect = stop_checker
        opener = threading.Thread(target=opened, daemon=True)
        closer = threading.Thread(target=client.stop, daemon=True)
        opener.start()
        try:
            self.assertTrue(entered.wait(1))
            closer.start()
            self.assertFalse(stopped.wait(0.1))
        finally:
            release.set()
            opener.join(timeout=1)
            if closer.ident is not None:
                closer.join(timeout=1)

        self.assertFalse(opener.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(stopped.is_set())
        self.assertFalse(checker.running)

    def test_stop_wakes_blocked_receive_without_peer_response(self):
        client = self.make_client()

        # Keep the peer silent while the receiver blocks on a real socket
        local, peer = socket.socketpair()
        self.addCleanup(peer.close)
        self.addCleanup(local.close)
        entered = threading.Event()
        received = []

        def receive():
            entered.set()
            received.append(local.recv(1))
            transport.on_socket_close()

        receiver = threading.Thread(target=receive, daemon=True)

        # Use SignalR's actual socket disposal, including its receiver join
        socket_client = WebSocketClient('ws://localhost:8092/ifcbHub')
        socket_client.sock = local
        socket_client.recv_thread = receiver
        closed = Mock(side_effect=client.stop)
        client.hub_connection.on_close(closed)
        transport = self.attach_transport(client, socket_client)
        transport.handle_reconnect = Mock(
            side_effect=AssertionError('explicit stop must not retry')
        )
        receiver.start()
        self.assertTrue(entered.wait(1))

        # Cleanup must finish while the peer remains connected and silent
        closer = threading.Thread(target=client.stop, daemon=True)
        closer.start()
        closer.join(timeout=1)
        try:
            self.assertFalse(
                closer.is_alive(), 'stop waited for the unreachable peer'
            )
            self.assertFalse(receiver.is_alive())
            self.assertEqual(received, [b''])
            self.assertEqual(local.fileno(), -1)
            self.assertTrue(transport.manually_closing)
            self.assertTrue(transport.is_disconnected())
            transport.connection_checker.stop.assert_called_once_with()
            transport.handle_reconnect.assert_not_called()
            closed.assert_called_once_with()
        finally:
            peer.close()  # Release a broken implementation too
            closer.join(timeout=1)
            receiver.join(timeout=1)

    def test_stop_closes_socket_before_receiver_started(self):
        client = self.make_client()
        local = socket.socket()
        self.addCleanup(local.close)
        socket_client = WebSocketClient('ws://localhost:8092/ifcbHub')
        socket_client.sock = local
        transport = self.attach_transport(client, socket_client)
        transport.state = type(transport.state).connecting

        # Failed connection setup can leave a socket without a receiver thread
        client.stop()
        self.assertEqual(local.fileno(), -1)
        self.assertTrue(transport.is_disconnected())
        transport.connection_checker.stop.assert_called_once_with()

    def test_stop_from_receiver_reports_close_once(self):
        client = self.make_client()
        local, peer = socket.socketpair()
        self.addCleanup(peer.close)
        self.addCleanup(local.close)
        socket_client = WebSocketClient('ws://localhost:8092/ifcbHub')
        socket_client.sock = local
        closed = Mock()
        client.hub_connection.on_close(closed)
        transport = self.attach_transport(client, socket_client)

        # A callback running on the receiver can stop without joining itself
        receiver = threading.Thread(
            target=client.stop, name=socket_client.thread_name, daemon=True
        )
        socket_client.recv_thread = receiver
        receiver.start()
        receiver.join(timeout=1)
        self.assertFalse(receiver.is_alive())
        self.assertEqual(local.fileno(), -1)
        self.assertTrue(transport.is_disconnected())
        closed.assert_called_once_with()
        transport.on_socket_close()
        closed.assert_called_once_with()
