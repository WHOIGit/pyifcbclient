import unittest
import unittest.mock as mock

from collections import defaultdict
from types import SimpleNamespace

from ifcbclient import IFCBClient


class TestClientLifecycle(unittest.TestCase):
    def setUp(self):
        self.events = defaultdict(list)
        self.hub = mock.Mock()
        self.hub.on.side_effect = self.register_event
        for event in ('open', 'close', 'error', 'reconnect'):
            getattr(self.hub, 'on_' + event).side_effect = (
                lambda callback, event=event:
                self.events[event].append(callback)
            )

        # Model transport cleanup separately from the IFCB protocol events
        self.socket_client = mock.Mock()
        self.transport = SimpleNamespace(
            _client=self.socket_client,
            connection_checker=mock.Mock(),
            manually_closing=False,
            on_socket_close=mock.Mock(side_effect=lambda: self.emit('close')),
        )
        self.hub.transport = self.transport

        # Capture callbacks on the hub that the client actually constructs
        self.builder = mock.Mock()
        self.builder.with_url.return_value = self.builder
        self.builder.configure_logging.return_value = self.builder
        self.builder.with_automatic_reconnect.return_value = self.builder
        self.builder.build.return_value = self.hub
        patcher = mock.patch(
            'ifcbclient.ifcbclient.HubConnectionBuilder',
            return_value=self.builder,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def register_event(self, event, callback):
        self.events[event].append(callback)

    def emit(self, event, *args):
        for callback in tuple(self.events[event]):
            callback(*args)

    def make_client(self, **kwargs):
        return IFCBClient(
            'ws://localhost:8092/ifcbHub', '111-111-152', **kwargs
        )

    def test_reconnection_remains_enabled_by_default(self):
        client = self.make_client()
        self.builder.with_automatic_reconnect.assert_called_once()
        client.connect()
        self.hub.start.assert_called_once_with()

    def test_reconnection_can_be_disabled(self):
        client = self.make_client(auto_reconnect=False)
        self.builder.with_automatic_reconnect.assert_not_called()
        client.connect()
        self.hub.start.assert_called_once_with()

    def test_registration_still_initializes_the_host(self):
        client = self.make_client(auto_reconnect=False)
        registered = mock.Mock()
        client.on_started(registered)
        self.emit('open')
        self.emit('startedAsClient', [])
        self.assertEqual(self.hub.send.call_args_list, [
            mock.call('startAsClient', ['111-111-152']),
            mock.call(
                'relayMessageToHost', ['111-111-152', 'Client connected.']
            ),
            mock.call('relayMessageToHost', ['111-111-152', 'refresh']),
        ])
        registered.assert_called_once_with([])

    def test_heartbeat_and_error_callbacks_receive_the_original_event(self):
        client = self.make_client(auto_reconnect=False)
        heartbeat = mock.Mock()
        error = mock.Mock()
        client.on_heartbeat(heartbeat)
        client.on_error(error)
        self.emit('heartbeat', [123])
        failure = RuntimeError('transport failed')
        self.emit('error', failure)
        heartbeat.assert_called_once_with([123])
        error.assert_called_once_with(failure)

    def test_close_callback_reports_transport_closure(self):
        client = self.make_client(auto_reconnect=False)
        first = mock.Mock()
        second = mock.Mock()
        client.on_close(first)
        client.on_close(second)
        self.emit('close')
        first.assert_called_once_with()
        second.assert_called_once_with()

    def test_server_disconnect_leaves_cleanup_to_external_manager(self):
        client = self.make_client(auto_reconnect=False)
        closed = mock.Mock()
        client.on_close(closed)
        self.emit('disconnect', ['host unavailable'])
        closed.assert_called_once_with(['host unavailable'])
        self.hub.send.assert_not_called()
        self.socket_client.dispose.assert_not_called()

        # The manager can stop after the receive callback has returned
        client.stop()
        self.socket_client.dispose.assert_called_once_with()
        self.assertEqual(closed.call_args_list, [
            mock.call(['host unavailable']), mock.call(),
        ])

    def test_server_disconnect_preserves_default_cleanup(self):
        client = self.make_client()
        closed = mock.Mock()
        client.on_close(closed)
        self.emit('disconnect', [])
        self.assertEqual(closed.call_args_list, [mock.call([]), mock.call()])
        self.hub.send.assert_called_once_with(
            'relayMessageToHost', ['111-111-152', 'Client disconnected.']
        )
        self.socket_client.dispose.assert_called_once_with()

    def test_disconnect_observer_failure_still_cleans_up(self):
        client = self.make_client()
        closed = mock.Mock(side_effect=[ValueError('observer failed'), None])
        client.on_close(closed)
        with self.assertRaisesRegex(ValueError, 'observer failed'):
            self.emit('disconnect', [])
        self.assertEqual(closed.call_args_list, [mock.call([]), mock.call()])
        self.hub.send.assert_called_once_with(
            'relayMessageToHost', ['111-111-152', 'Client disconnected.']
        )
        self.socket_client.dispose.assert_called_once_with()

    def test_stop_is_local_idempotent_and_reports_transport_close(self):
        client = self.make_client(auto_reconnect=False)
        closed = mock.Mock(side_effect=client.stop)
        client.on_close(closed)

        # A close callback can call stop again while cleanup is in progress
        client.stop()
        client.stop()
        self.socket_client.dispose.assert_called_once_with()
        self.transport.connection_checker.stop.assert_called_once_with()
        self.hub.send.assert_not_called()
        closed.assert_called_once_with()

    def test_client_can_connect_and_stop_again(self):
        client = self.make_client(auto_reconnect=False)
        client.connect()
        client.stop()
        client.connect()
        client.stop()
        self.assertEqual(self.hub.start.call_args_list, [
            mock.call(), mock.call(),
        ])
        self.assertEqual(self.socket_client.dispose.call_args_list, [
            mock.call(), mock.call(),
        ])
        self.hub.send.assert_not_called()

    def test_disconnect_preserves_farewell_and_stops_after_send_failure(self):
        client = self.make_client()
        self.hub.send.side_effect = OSError('broken socket')
        with self.assertRaises(OSError):
            client.disconnect()
        self.hub.send.assert_called_once_with(
            'relayMessageToHost', ['111-111-152', 'Client disconnected.']
        )
        self.socket_client.dispose.assert_called_once_with()
