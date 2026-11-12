"""Track SignalR behavior that requires a client-library workaround."""

import json
import threading
import unittest

from types import SimpleNamespace

from signalrcore.hub_connection_builder import HubConnectionBuilder
from signalrcore.protocol.json_hub_protocol import JsonHubProtocol
from signalrcore.transport.websockets.websocket_transport import (
    WebsocketTransport,
)


class TestSignalRKeepalive(unittest.TestCase):
    def setUp(self):
        self.pings = {}
        for auto_reconnect in (True, False):
            builder = HubConnectionBuilder().with_url(
                'ws://localhost:8092/ifcbHub'
            )
            if auto_reconnect:
                builder.with_automatic_reconnect({
                    'type': 'raw',
                    'keep_alive_interval': 0,
                    'reconnect_interval': 1,
                    'max_attempts': 1,
                })
            hub = builder.build()
            transport = WebsocketTransport(
                url=hub.url,
                protocol=JsonHubProtocol(),
                keep_alive_interval=0,
                reconnection_handler=hub.kwargs.get('reconnection_handler'),
                on_open=lambda: None,
            )
            transport.connection_checker.sleep = 0
            self.addCleanup(transport.connection_checker.stop)

            # Observe encoded pings while retaining the real keepalive thread
            ping = threading.Event()
            self.pings[auto_reconnect] = ping

            def send(message, opcode, ping=ping):
                if opcode == 1 and json.loads(message.rstrip('\x1e')) == {
                    'type': 6,
                }:
                    ping.set()

            transport._client = SimpleNamespace(send=send)
            transport.state = type(transport.state).connecting
            transport.on_message(None, '{}\x1e')
            self.assertTrue(transport.handshake_received)
            self.assertTrue(transport.is_connected())

        # Validate the control outside the expected-failure test method
        self.assertTrue(self.pings[True].wait(1), 'Control emitted no ping')

    @unittest.expectedFailure
    def test_keepalive_without_automatic_reconnection(self):

        # SignalR 1.0.2 incorrectly couples keepalive to automatic reconnection
        self.assertTrue(
            self.pings[False].wait(1),
            'Disabling retries must not disable outbound keepalive',
        )
