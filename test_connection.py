import threading
import time
import unittest

from unittest.mock import patch

from ifcbclient import connection


class FakeClient:
    def __init__(self, manager):
        self.registered = manager._guard(self, manager._registered)
        self.heartbeat = manager._guard(self, manager._heartbeat)
        self.disconnected = manager._guard(self, manager._disconnect)
        self.failure = None
        self.sent = []
        self.started = threading.Event()
        self.stopped = threading.Event()

    def connect(self):
        self.started.set()  # Transport open, but not registered yet

    def relay_message_to_host(self, command):
        self.sent.append(command)

    def stop(self):

        # Exercise a close callback during cleanup; the manager must not hold
        # its lock while joining a transport's callback thread.
        thread = threading.Thread(target=self.disconnected)
        thread.start()
        thread.join(timeout=1)
        if thread.is_alive():
            raise AssertionError('cleanup deadlocked with a callback')
        self.stopped.set()


class TestConnectionManager(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.status = []
        self.logs = []
        self.retry_delays = []
        self.now = 100.0
        self.factory_error = False
        self.auto_reconnect = True

        # Shorten waits while keeping heartbeat time under test control
        self.patches = [
            patch.object(connection, 'RETRY_INTERVAL', 0.02),
            patch.object(connection, 'MAX_RETRY_INTERVAL', 0.05),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

        # Start the manager with fake transport and observable status changes
        self.manager = connection.ConnectionManager(
            owner=self,
            clock=lambda: self.now,
        )
        self.status.append(self.manager.ready)
        self.manager.start()
        self.addCleanup(self.shutdown)
        self.first = self.await_client(0)

    def _notify(self, client, event, *args):
        if event == 'ready':
            self.status.append(args[0])
        elif event == 'error':
            self.logs.append(str(args[0]))
        elif event == 'retry':
            self.retry_delays.append(args[0])

    def _make_connection(self):
        if self.factory_error:
            raise OSError('unavailable')
        client = FakeClient(self.manager)
        self.clients.append(client)
        return client

    def await_client(self, index):
        deadline = time.monotonic() + 2
        while len(self.clients) <= index and time.monotonic() < deadline:
            time.sleep(0.005)
        self.assertGreater(len(self.clients), index)
        client = self.clients[index]
        self.assertTrue(client.started.wait(1))
        return client

    def shutdown(self):
        worker = self.manager.worker
        self.manager.close()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive(), 'manager did not shut down')

    def advance(self, seconds):
        with self.manager.condition:
            self.now += seconds
            self.manager.condition.notify_all()

    def test_heartbeat_expiry_disconnects_and_reconnect_requires_registration(
        self,
    ):
        self.first.registered([])
        self.advance(connection.HEARTBEAT_TIMEOUT - 1)
        self.first.heartbeat([])
        self.advance(connection.HEARTBEAT_TIMEOUT - 1)
        self.assertIsNone(self.manager.send('still alive', timeout=0))

        # Expiry retires the old client and starts an unregistered replacement
        self.advance(1)
        second = self.await_client(1)
        self.assertEqual(self.status, [False, True, False])
        self.assertTrue(self.first.stopped.is_set())
        second.heartbeat([])
        with self.assertRaises(ConnectionError):
            self.manager.send('too early', timeout=0)
        second.registered([])
        self.assertIsNone(self.manager.send('recovered', timeout=0))
        self.assertEqual(second.sent, ['recovered'])

    def test_shutdown_while_connecting_retires_callbacks(self):
        self.shutdown()
        self.first.registered([])
        self.first.heartbeat([])
        self.assertEqual(self.status, [False])
        self.assertTrue(self.first.stopped.is_set())

    def test_shutdown_interrupts_backoff(self):
        with patch.object(connection, 'RETRY_INTERVAL', 60):

            # Successful registration resets the delay to this long interval
            self.first.registered([])
            self.first.disconnected()
            self.assertTrue(self.first.stopped.wait(1))
            self.shutdown()

    def test_retry_delay_is_capped_and_registration_resets_it(self):
        self.first.disconnected()
        for index in range(1, 5):
            client = self.await_client(index)
            if index < 4:
                client.disconnected()

        # Even an immediately lost registration resets accumulated backoff
        client.registered([])
        client.disconnected()
        self.await_client(5)
        self.assertEqual(len(self.retry_delays), 5)
        for actual, expected in zip(self.retry_delays,
                                    [0.02, 0.03, 0.045, 0.05, 0.02]):
            self.assertAlmostEqual(actual, expected)

    def test_constructor_failure_retries_and_shutdown_remains_available(self):
        self.factory_error = True
        self.first.disconnected()
        deadline = time.monotonic() + 1
        while (
            not any('unavailable' in log for log in self.logs)
            and time.monotonic() < deadline
        ):
            time.sleep(0.005)
        self.assertTrue(any('unavailable' in log for log in self.logs))
        self.shutdown()

    def test_transport_start_failure_releases_client_before_retry(self):
        make_connection = self._make_connection

        def fail_once():
            client = make_connection()

            def fail():
                client.started.set()
                raise OSError('transport refused connection')

            client.connect = fail
            self._make_connection = make_connection
            return client

        with self.manager.condition:
            self._make_connection = fail_once
            self.first.disconnected()
        failed = self.await_client(1)
        recovered = self.await_client(2)
        self.assertTrue(failed.stopped.is_set())
        self.assertEqual(self.status, [False])
        recovered.registered([])
        self.assertIsNone(self.manager.send('recovered', timeout=0))
