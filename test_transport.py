"""Check SignalR keepalive and cleanup with real local sockets."""

import socket
import threading
import unittest

from types import SimpleNamespace
from unittest.mock import Mock

from ifcbclient import IFCBClient
from signalrcore.transport.websockets.websocket_client import WebSocketClient
from signalrcore.transport.websockets.websocket_transport import (
    WebsocketTransport,
)


class TestClientTransport(unittest.TestCase):
    def make_client(self, **kwargs):
        return IFCBClient(
            'ws://localhost:8092/ifcbHub', '111-111-152', **kwargs
        )

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

    def test_keepalive_starts_when_automatic_reconnection_is_disabled(self):
        client = self.make_client(auto_reconnect=False)
        hub = client.hub_connection
        self.assertIsNone(hub.kwargs.get('reconnection_handler'))
        checker = Mock()
        hub.transport = SimpleNamespace(connection_checker=checker)
        hub.send = Mock()

        client.on_connect()
        checker.start.assert_called_once_with()
        hub.send.assert_called_once_with('startAsClient', ['111-111-152'])

    def test_default_reconnection_does_not_start_keepalive_twice(self):
        client = self.make_client()
        hub = client.hub_connection
        self.assertIsNotNone(hub.kwargs.get('reconnection_handler'))
        checker = Mock()
        hub.transport = SimpleNamespace(connection_checker=checker)
        hub.send = Mock()

        # SignalR starts its own keepalive when automatic retries are enabled
        client.on_connect()
        checker.start.assert_not_called()
        hub.send.assert_called_once_with('startAsClient', ['111-111-152'])

    def test_stop_before_connect_is_harmless(self):
        client = self.make_client(auto_reconnect=False)
        self.assertIsNone(client.hub_connection.transport)
        client.stop()
        client.stop()
        self.assertIsNone(client.hub_connection.transport)

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
        transport = self.attach_transport(client, socket_client)
        transport.handle_reconnect = Mock(
            side_effect=AssertionError('explicit stop must not retry')
        )
        closed = Mock(side_effect=client.stop)
        client.on_close(closed)
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
        client = self.make_client(auto_reconnect=False)
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

    def test_stop_from_receiver_reports_close_and_allows_new_attempt(self):
        client = self.make_client(auto_reconnect=False)
        local, peer = socket.socketpair()
        self.addCleanup(peer.close)
        self.addCleanup(local.close)
        socket_client = WebSocketClient('ws://localhost:8092/ifcbHub')
        socket_client.sock = local
        transport = self.attach_transport(client, socket_client)
        closed = Mock()
        client.on_close(closed)

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

        # Reuse must negotiate again instead of seeing the old hub as connected
        client.hub_connection._negotiate = Mock(
            side_effect=OSError('new connection attempt')
        )
        with self.assertRaisesRegex(OSError, 'new connection attempt'):
            client.connect()
        client.hub_connection._negotiate.assert_called_once_with()
