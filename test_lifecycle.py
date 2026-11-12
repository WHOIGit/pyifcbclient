import queue
import threading
import unittest
import unittest.mock as mock

from collections import defaultdict

from ifcbclient import IFCBClient
from ifcbclient import connection


# Model one SignalR attempt, with callbacks retained after its retirement
class FakeHub:
    def __init__(self, started):
        self.events = defaultdict(list)
        self.send = mock.Mock()
        self.start = mock.Mock(side_effect=lambda: started.put(self))
        self.cleaned = threading.Event()
        self.transport = mock.Mock()
        self.transport.manually_closing = False
        self.transport.on_socket_close.side_effect = self.close_transport

    def on(self, event, callback):
        self.events[event].append(callback)

    def on_open(self, callback):
        self.on('open', callback)

    def on_close(self, callback):
        self.on('close', callback)

    def on_error(self, callback):
        self.on('error', callback)

    def emit(self, event, *args):
        for callback in tuple(self.events[event]):
            callback(*args)

    def close_transport(self):
        self.emit('close')
        self.cleaned.set()


class TestClientLifecycle(unittest.TestCase):
    def setUp(self):
        self.started = queue.Queue()
        self.hubs = []
        self.clients = []

        # Every retry builds a new hub, retaining callbacks on the old one
        self.builder = mock.Mock()
        self.builder.with_url.return_value = self.builder
        self.builder.configure_logging.return_value = self.builder
        self.builder.build.side_effect = self.build_hub
        patches = [
            mock.patch(
                'ifcbclient.ifcbclient.HubConnectionBuilder',
                return_value=self.builder,
            ),
            mock.patch.object(connection, 'RETRY_INTERVAL', 0.01),
            mock.patch.object(connection, 'MAX_RETRY_INTERVAL', 0.02),
            mock.patch.object(connection, 'REGISTRATION_TIMEOUT', 0.2),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.stop_clients)

    def build_hub(self):
        hub = FakeHub(self.started)
        self.hubs.append(hub)
        return hub

    def make_client(self, **kwargs):
        client = IFCBClient(
            'ws://localhost:8092/ifcbHub', '111-111-152', **kwargs
        )
        self.clients.append(client)
        return client

    def next_hub(self):
        try:
            return self.started.get(timeout=2)
        except queue.Empty:
            self.fail('client did not start a connection attempt')

    def stop_clients(self):
        for client in self.clients:
            client.stop()
            worker = client._connection.worker
            if worker is not None:
                worker.join(timeout=1)
                self.assertFalse(worker.is_alive(), 'client did not stop')
            self.flush_callbacks(client)

    # Wait for queued callbacks through a public readiness subscription
    def flush_callbacks(self, client):
        delivered = threading.Event()
        client.on_ready(lambda _: delivered.set())
        self.assertTrue(delivered.wait(1), 'callbacks did not complete')

    def register(self, hub):
        hub.emit('open')
        hub.emit('startedAsClient', [])
        client = next(c for c in self.clients if c.hub_connection is hub)
        self.wait_registered(client)

    def wait_registered(self, client):
        with client._connection.condition:
            self.assertTrue(client._connection.condition.wait_for(
                lambda: client._connection.ready, timeout=1
            ), 'client did not register')
        self.flush_callbacks(client)

    def test_disabling_reconnection_stops_after_one_attempt(self):
        client = self.make_client(auto_reconnect=False)
        closed = mock.Mock()
        connecting = mock.Mock()
        retry = mock.Mock()
        client.on_close(closed)
        client.on_connecting(connecting)
        client.on_retry(retry)
        client.connect(background=True)
        hub = self.next_hub()
        hub.emit('disconnect', ['host unavailable'])
        client._connection.worker.join(timeout=1)
        self.assertFalse(client._connection.worker.is_alive())
        self.assertTrue(hub.cleaned.is_set())
        self.assertEqual(len(self.hubs), 1)
        self.flush_callbacks(client)
        closed.assert_called_once_with(['host unavailable'])
        connecting.assert_called_once_with()
        retry.assert_not_called()
        self.builder.with_automatic_reconnect.assert_not_called()

    def test_registration_grants_readiness_after_initialization(self):
        client = self.make_client(auto_reconnect=False)
        ready = []
        registered = mock.Mock()
        reconnected = mock.Mock()
        client.on_ready(ready.append)
        client.on_started(registered)
        client.on_reconnect(reconnected)
        client.connect(background=True)
        hub = self.next_hub()
        hub.emit('open')
        hub.emit('heartbeat', [123])
        self.flush_callbacks(client)
        self.assertEqual(ready, [False])
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('too early', timeout=0)

        hub.emit('startedAsClient', [])
        self.wait_registered(client)

        # A repeated acknowledgement must not initialize or notify twice
        hub.emit('startedAsClient', [])
        self.flush_callbacks(client)
        self.assertEqual(ready, [False, True])
        self.assertEqual(hub.send.call_args_list, [
            mock.call('startAsClient', ['111-111-152']),
            mock.call(
                'relayMessageToHost', ['111-111-152', 'Client connected.']
            ),
            mock.call('relayMessageToHost', ['111-111-152', 'refresh']),
        ])
        registered.assert_called_once_with([])
        reconnected.assert_not_called()
        late_observer = mock.Mock()
        client.on_ready(late_observer)
        self.flush_callbacks(client)
        late_observer.assert_called_once_with(True)

    def test_failed_initialization_never_grants_readiness(self):
        client = self.make_client(auto_reconnect=False)
        ready = []
        registered = mock.Mock()
        error = mock.Mock()
        client.on_ready(ready.append)
        client.on_started(registered)
        client.on_error(error)
        client.connect(background=True)
        hub = self.next_hub()
        hub.emit('open')
        self.flush_callbacks(client)
        hub.send.side_effect = OSError('initial refresh failed')
        hub.emit('startedAsClient', [])
        self.assertTrue(hub.cleaned.wait(1))
        self.flush_callbacks(client)
        self.assertEqual(ready, [False])
        registered.assert_not_called()
        error.assert_called_once()

    def test_open_send_failure_is_reported_after_synchronous_close(self):
        client = self.make_client(auto_reconnect=False)
        error = mock.Mock()
        client.on_error(error)
        client.connect(background=True)
        hub = self.next_hub()
        failure = OSError('startAsClient failed')

        def close_then_fail(*args):
            hub.emit('close')
            raise failure

        hub.send.side_effect = close_then_fail
        hub.emit('open')
        self.assertTrue(hub.cleaned.wait(1))
        self.flush_callbacks(client)
        error.assert_called_once_with(failure)

    def test_callbacks_survive_reconnect_and_stale_events_are_ignored(self):
        client = self.make_client()
        ready = []
        connecting = mock.Mock()
        retry = mock.Mock()
        registered = mock.Mock()
        heartbeat = mock.Mock()
        reconnected = mock.Mock()
        closed = mock.Mock()
        parsed = mock.Mock()
        raw = mock.Mock()
        client.on_ready(ready.append)
        client.on_connecting(connecting)
        client.on_retry(retry)
        client.on_started(registered)
        client.on_heartbeat(heartbeat)
        client.on_reconnect(reconnected)
        client.on_close(closed)
        client.on(('syringetrack',), parsed)
        client.on_any_message(raw)
        client.connect(background=True)
        first = self.next_hub()
        self.register(first)
        first.emit('close')
        second = self.next_hub()
        self.assertIsNot(first, second)
        self.assertTrue(first.cleaned.is_set())
        self.builder.with_automatic_reconnect.assert_not_called()
        self.flush_callbacks(client)
        self.assertEqual(ready, [False, True, False])
        self.assertEqual(connecting.call_count, 2)
        retry.assert_called_once_with(0.01)
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('offline', timeout=0)
        old_sends = list(first.send.call_args_list)

        # Retired events cannot initialize, publish data, or revive readiness
        for event, args in [
            ('open', ()),
            ('startedAsClient', ([],)),
            ('heartbeat', ([123],)),
            ('messageRelayed', ([0, 0, 0, 'syringetrack:1.5'],)),
            ('error', (RuntimeError('old error'),)),
        ]:
            first.emit(event, *args)
        self.flush_callbacks(client)
        self.assertEqual(first.send.call_args_list, old_sends)
        self.assertEqual(ready, [False, True, False])
        parsed.assert_not_called()
        raw.assert_not_called()
        heartbeat.assert_not_called()

        # A replacement must register before reporting reconnection
        self.register(second)
        first.emit('close')
        second.emit('heartbeat', [456])
        message = [0, 0, 1, 'syringetrack:2.5']
        second.emit('messageRelayed', message)
        self.flush_callbacks(client)
        self.assertEqual(ready, [False, True, False, True])
        self.assertEqual(registered.call_args_list, [mock.call([])] * 2)
        reconnected.assert_called_once_with()
        closed.assert_called_once_with()
        heartbeat.assert_called_once_with([456])
        raw.assert_called_once_with(message)
        parsed.assert_called_once_with('syringetrack', 2.5)
        self.assertIsNone(client.relay_message_to_host('refresh', timeout=0))

    def test_initial_failed_attempt_is_not_a_reconnection(self):
        client = self.make_client()
        reconnected = mock.Mock()
        client.on_reconnect(reconnected)
        client.connect(background=True)
        self.next_hub().emit('close')
        self.register(self.next_hub())
        reconnected.assert_not_called()

    def test_send_failure_clears_readiness_and_reports_failure(self):
        client = self.make_client()
        error = mock.Mock()
        ready = []
        client.on_error(error)
        client.on_ready(ready.append)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        hub.send.side_effect = OSError('broken socket')
        with self.assertRaisesRegex(OSError, 'broken socket'):
            client.relay_message_to_host('sample', timeout=0)
        self.assertTrue(hub.cleaned.wait(1))
        self.flush_callbacks(client)
        self.assertEqual(ready, [False, True, False])
        error.assert_called_once()
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('offline', timeout=0)
        self.next_hub()

    def test_synchronous_close_during_send_reports_failure(self):
        client = self.make_client(auto_reconnect=False)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        hub.send.side_effect = lambda *_: hub.emit('close')
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('sample', timeout=0)
        self.assertTrue(hub.cleaned.wait(1))

    def test_missing_registration_times_out_without_retry_when_disabled(self):
        client = self.make_client(auto_reconnect=False)
        error = mock.Mock()
        client.on_error(error)
        client.connect(background=True)
        hub = self.next_hub()
        hub.emit('open')
        hub.emit('heartbeat', [123])
        client._connection.worker.join(timeout=1)
        self.assertFalse(client._connection.worker.is_alive())
        self.assertTrue(hub.cleaned.is_set())
        self.assertEqual(len(self.hubs), 1)
        self.flush_callbacks(client)
        error.assert_called_once()
        self.assertIsInstance(error.call_args.args[0], TimeoutError)
        self.assertEqual(str(error.call_args.args[0]),
                         'IFCB registration timed out')

    def test_stop_is_local_idempotent_and_reports_close_once(self):
        client = self.make_client()
        closed = mock.Mock()
        retry = mock.Mock()
        ready = []
        client.on_close(closed)
        client.on_retry(retry)
        client.on_ready(ready.append)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        hub.send.reset_mock()
        client.stop()
        client.stop()
        client.disconnect()
        self.assertTrue(hub.cleaned.is_set())
        hub.transport._client.dispose.assert_called_once_with()
        hub.transport.connection_checker.stop.assert_called_once_with()
        hub.send.assert_not_called()
        self.flush_callbacks(client)
        closed.assert_called_once_with()
        retry.assert_not_called()
        self.assertEqual(ready, [False, True, False])
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('after stop', timeout=0)

    def test_client_can_explicitly_connect_again_after_stop(self):
        client = self.make_client(auto_reconnect=False)
        registered = mock.Mock()
        client.on_started(registered)
        client.connect(background=True)
        first = self.next_hub()
        self.register(first)
        client.stop()
        client.connect(background=True)
        second = self.next_hub()
        self.register(second)
        self.assertIsNot(first, second)
        self.assertTrue(first.cleaned.is_set())
        self.assertEqual(registered.call_count, 2)

    def test_late_error_is_delivered_while_cleanup_is_blocked(self):
        client = self.make_client(auto_reconnect=False)
        closed = threading.Event()
        cleaning = threading.Event()
        release = threading.Event()
        errors = queue.Queue()
        client.on_close(closed.set)
        client.on_error(errors.put)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        manager = client._connection
        attempt = manager.client
        dispatcher = manager.dispatcher

        def stop_checker():
            cleaning.set()
            release.wait(3)

        hub.transport.connection_checker.stop.side_effect = stop_checker
        closer = threading.Thread(target=client.stop, daemon=True)
        closer.start()
        try:
            self.assertTrue(cleaning.wait(1))
            self.assertTrue(closed.wait(1))
            dispatcher.join(timeout=1)
            self.assertFalse(dispatcher.is_alive())

            # A pending send may fail after close callbacks have drained
            failure = OSError('late send failure')
            manager.failed(attempt, failure)
            self.assertIs(errors.get(timeout=1), failure)
            self.assertFalse(hub.cleaned.is_set())
        finally:
            release.set()
            closer.join(timeout=1)
        self.assertFalse(closer.is_alive())
        self.assertTrue(hub.cleaned.is_set())

    def test_disconnect_sends_farewell_then_cleans_up(self):
        client = self.make_client(auto_reconnect=False)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        hub.send.reset_mock()
        client.disconnect()
        client.disconnect()
        hub.send.assert_called_once_with(
            'relayMessageToHost', ['111-111-152', 'Client disconnected.']
        )
        self.assertTrue(hub.cleaned.is_set())

    def test_stop_from_callbacks_does_not_deadlock(self):
        for event, subscribe, args in [
            ('startedAsClient', 'on_started', ([],)),
            ('heartbeat', 'on_heartbeat', ([123],)),
            ('messageRelayed', 'on_any_message',
             ([0, 0, 0, 'syringetrack:1.5'],)),
            ('close', 'on_close', ()),
            ('close', 'on_retry', ()),
        ]:
            with self.subTest(event=event, callback=subscribe):
                client = self.make_client(
                    auto_reconnect=(subscribe == 'on_retry')
                )
                stopped = threading.Event()

                def stop(*args, client=client, stopped=stopped):
                    client.stop()
                    stopped.set()

                getattr(client, subscribe)(stop)
                client.connect(background=True)
                hub = self.next_hub()
                hub.emit('open')
                if event != 'startedAsClient':
                    hub.emit('startedAsClient', [])
                    self.wait_registered(client)

                # A retry observer can cancel a long wait by stopping
                if subscribe == 'on_retry':
                    with client._connection.condition:
                        client._connection.retry = 60

                # The receiver can enqueue callbacks without waiting for them
                receiver = threading.Thread(
                    target=lambda: hub.emit(event, *args), daemon=True
                )
                receiver.start()
                receiver.join(timeout=1)
                self.assertFalse(receiver.is_alive(), 'receiver deadlocked')
                self.assertTrue(stopped.wait(1), 'stop callback deadlocked')
                self.assertTrue(hub.cleaned.wait(1))

    def test_registration_observer_failure_keeps_connection_usable(self):
        client = self.make_client(auto_reconnect=False)
        first = mock.Mock(side_effect=ValueError('observer failed'))
        second = mock.Mock()
        client.on_started(first)
        client.on_started(second)
        client.connect(background=True)
        hub = self.next_hub()
        with self.assertLogs('ifcbclient', level='ERROR'):
            self.register(hub)
        first.assert_called_once_with([])
        second.assert_called_once_with([])
        self.assertFalse(hub.cleaned.is_set())
        self.assertIsNone(client.relay_message_to_host('refresh', timeout=0))

    def test_stop_from_initial_readiness_callback_does_not_deadlock(self):
        client = self.make_client(auto_reconnect=False)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        stopped = threading.Event()

        def stop(_):
            client.stop()
            stopped.set()

        client.on_ready(stop)
        self.assertTrue(stopped.wait(1), 'readiness callback deadlocked')
        self.assertTrue(hub.cleaned.wait(1))

    def test_stopping_in_readiness_observer_cannot_publish_stale_ready(self):
        client = self.make_client(auto_reconnect=False)
        ready = []
        registered = mock.Mock()
        client.on_ready(lambda value: client.stop() if value else None)
        client.on_ready(ready.append)
        client.on_started(registered)
        client.connect(background=True)
        hub = self.next_hub()
        hub.emit('open')
        hub.emit('startedAsClient', [])
        self.assertTrue(hub.cleaned.wait(1))
        self.flush_callbacks(client)
        self.assertNotIn(True, ready)
        self.assertFalse(ready[-1])
        registered.assert_not_called()
        with self.assertRaises(ConnectionError):
            client.relay_message_to_host('after stop', timeout=0)

    def test_stop_from_parsed_message_callback_does_not_deadlock(self):
        client = self.make_client(auto_reconnect=False)
        raw = mock.Mock()
        client.on(('syringetrack',), lambda *_: client.stop())
        client.on_any_message(raw)
        client.connect(background=True)
        hub = self.next_hub()
        self.register(hub)
        receiver = threading.Thread(
            target=lambda: hub.emit(
                'messageRelayed', [0, 0, 0, 'syringetrack:1.5']
            ),
            daemon=True,
        )
        receiver.start()
        receiver.join(timeout=1)
        self.assertFalse(receiver.is_alive(), 'receiver deadlocked')
        self.assertTrue(hub.cleaned.wait(1))
        self.flush_callbacks(client)
        raw.assert_not_called()
