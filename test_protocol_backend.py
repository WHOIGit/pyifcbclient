import base64
import hashlib
import json
import queue
import socket
import threading
import time
import unittest

from unittest.mock import patch

from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect
from websockets.sync.server import serve

from ifcbclient import IFCBClient, connection, signalr


SERIAL = '111-111-152'


# Exercise real WebSocket upgrades and frames with an independent server
class TestProtocolBackend(unittest.TestCase):
    def setUp(self):
        self.clients = []
        self.peers = []
        self.peer_threads = []
        self.servers = []
        self.server_threads = []
        self.server_errors = queue.Queue()
        self.raw_peers = []
        self.workers = []

    def tearDown(self):

        # Release blocked workers after assertions, including failed tests
        leaked = []
        for peer in self.raw_peers:
            try:
                peer.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # The client may already have reset the connection
        for worker in self.workers:
            worker.join(2)
            if worker.is_alive():
                leaked.append(worker)
        for client in self.clients:
            client.stop()
            receiver = getattr(client, 'receiver', None)
            if receiver is not None and receiver.is_alive():
                leaked.append(receiver)
        for peer in self.peers:
            peer.close()
        for server in self.servers:
            server.shutdown()
        for thread in self.server_threads + self.peer_threads:
            thread.join(2)
            if thread.is_alive():
                leaked.append(thread)
        self.assertEqual(leaked, [], 'test or transport worker leaked')
        if not self.server_errors.empty():
            raise self.server_errors.get_nowait()

    def server(self, handler):
        def checked(peer):
            self.peers.append(peer)
            self.peer_threads.append(threading.current_thread())
            try:
                handler(peer)
            except ConnectionClosed:
                pass  # Local stop closes peers waiting for another message
            except Exception as error:
                self.server_errors.put(error)

        server = serve(
            checked, '127.0.0.1', 0, compression=None,
            max_size=None, close_timeout=0.1,
        )
        self.servers.append(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.server_threads.append(thread)
        thread.start()
        return f'ws://127.0.0.1:{server.socket.getsockname()[1]}/ifcbHub'

    def handshake(self, peer, response='{}\x1e'):
        request = peer.recv(timeout=2)
        self.assertEqual(json.loads(request[:-1]),
                         {'protocol': 'json', 'version': 1})
        self.assertTrue(request.endswith('\x1e'))
        peer.send(response)

    def wait_closed(self, peer):
        while True:
            peer.recv(timeout=3)

    def attempt(self, url):
        client = signalr.SignalRClient(url, SERIAL)
        self.clients.append(client)
        return client

    def test_split_records_and_large_fragmented_payload(self):
        payload = 'triggerimage:' + '\u03bc' * (1024 * 1024)
        sequence = 2 ** 63 + 123
        invocation = json.dumps({
            'type': 1, 'target': 'messageRelayed',
            'arguments': [SERIAL, 'host', sequence, payload],
        }, ensure_ascii=False) + '\x1e'

        def peer_script(peer):
            self.handshake(peer, '{}\x1e' + invocation[:11])
            peer.send('')
            peer.send([invocation[11:65000], invocation[65000:]])
            peer.send('{"type":6}\x1e' + json.dumps({
                'type': 1, 'target': 'heartbeat',
                'arguments': ['2026-11-12T00:00:00Z'],
            }) + '\x1e')
            self.wait_closed(peer)

        client = self.attempt(self.server(peer_script))
        messages = queue.Queue()
        heartbeats = queue.Queue()
        client.on('messageRelayed', messages.put)
        client.on('heartbeat', heartbeats.put)
        client.connect()
        self.assertEqual(messages.get(timeout=3),
                         [SERIAL, 'host', sequence, payload])
        self.assertEqual(heartbeats.get(timeout=2), ['2026-11-12T00:00:00Z'])

    def test_handshake_error_and_timeout_fail_startup(self):
        def rejected(peer):
            self.handshake(peer, '{"error":"Protocol rejected"}\x1e')
            self.wait_closed(peer)

        client = self.attempt(self.server(rejected))
        with self.assertRaisesRegex(Exception, 'Protocol rejected'):
            client.connect()

        def silent(peer):
            peer.recv(timeout=2)
            self.wait_closed(peer)

        client = self.attempt(self.server(silent))
        start = time.monotonic()
        with patch.object(signalr, 'HANDSHAKE_TIMEOUT', 0.15):
            with self.assertRaises(TimeoutError):
                client.connect()
        self.assertLess(time.monotonic() - start, 1)

    def test_stop_before_socket_handoff_closes_without_opening(self):
        supplied = queue.Queue()
        resume = threading.Event()
        failures = []
        opened = []
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            listener.settimeout(2)
            client = self.attempt(
                f'ws://127.0.0.1:{listener.getsockname()[1]}/ifcbHub'
            )
            client.on('open', lambda: opened.append(True))
            factory = client._create_connection

            # Stop after TCP connects but before the factory owns the socket
            def paused_factory(sock, *args, **kwargs):
                supplied.put(sock)
                self.assertTrue(resume.wait(2))
                return factory(sock, *args, **kwargs)

            def start():
                try:
                    client.connect()
                except Exception as error:
                    failures.append(error)

            with patch.object(client, '_create_connection', paused_factory):
                worker = threading.Thread(target=start, daemon=True)
                self.workers.append(worker)
                worker.start()
                try:
                    peer, _ = listener.accept()
                    self.raw_peers.append(peer)
                    self.addCleanup(peer.close)
                    sock = supplied.get(timeout=2)
                    client.stop()
                finally:
                    resume.set()
                worker.join(2)
                self.assertFalse(worker.is_alive())
            self.assertEqual(len(failures), 1)
            self.assertIsInstance(failures[0], ConnectionError)
            self.assertEqual(opened, [])
            self.assertEqual(sock.fileno(), -1)

    def test_invocation_error_is_nonfatal_and_preserves_identifier(self):
        received = queue.Queue()

        def peer_script(peer):
            self.handshake(peer)
            invocation = json.loads(peer.recv(timeout=2)[:-1])
            received.put(invocation)
            peer.send(json.dumps({
                'type': 3, 'invocationId': invocation['invocationId'],
                'error': 'Command rejected',
            }) + '\x1e')
            invocation = json.loads(peer.recv(timeout=2)[:-1])
            received.put(invocation)
            peer.send(json.dumps({
                'type': 3, 'invocationId': invocation['invocationId'],
            }) + '\x1e')
            self.wait_closed(peer)

        client = self.attempt(self.server(peer_script))
        errors = queue.Queue()
        closed = threading.Event()
        client.on('error', errors.put)
        client.on('close', closed.set)
        client.connect()
        client.relay_message_to_host('bad command')
        invocation = received.get(timeout=2)
        failure = errors.get(timeout=2)
        self.assertEqual(invocation['arguments'], [SERIAL, 'bad command'])
        self.assertEqual(invocation['target'], 'relayMessageToHost')
        self.assertEqual(failure.invocation_id, invocation['invocationId'])
        self.assertIsInstance(failure, signalr.InvocationError)
        self.assertEqual(str(failure), 'Command rejected')
        self.assertFalse(closed.is_set())
        client.relay_message_to_host('refresh')
        self.assertEqual(received.get(timeout=2)['arguments'],
                         [SERIAL, 'refresh'])
        self.assertTrue(errors.empty())

    def test_keepalive_and_concurrent_commands_are_valid_separate_frames(self):
        count = 8
        commands = []
        ping = threading.Event()
        all_commands = threading.Event()

        def peer_script(peer):
            self.handshake(peer)
            while len(commands) < count or not ping.is_set():
                message = json.loads(peer.recv(timeout=2)[:-1])
                if message['type'] == 6:
                    ping.set()
                else:
                    self.assertEqual(message['type'], 1)
                    self.assertEqual(message['target'], 'relayMessageToHost')
                    commands.append(message)
                    if len(commands) == count:
                        all_commands.set()
            self.wait_closed(peer)

        client = self.attempt(self.server(peer_script))
        with patch.object(signalr, 'KEEPALIVE_INTERVAL', 0.02):
            client.connect()
            writers = [threading.Thread(
                target=client.relay_message_to_host, args=(f'command:{i}',),
            ) for i in range(count)]
            for writer in writers:
                writer.start()
            for writer in writers:
                writer.join(2)
                self.assertFalse(writer.is_alive())
            self.assertTrue(all_commands.wait(2))
            self.assertTrue(ping.wait(2))
        self.assertEqual({item['arguments'][1] for item in commands},
                         {f'command:{i}' for i in range(count)})
        self.assertEqual(len({item['invocationId'] for item in commands}),
                         count)

    def test_server_closure_reports_errors_and_closes_once(self):
        for name, message in [
            ('websocket', None),
            ('signalr', {'type': 7}),
            ('signalr_error', {'type': 7, 'error': 'Server stopping'}),
        ]:
            with self.subTest(close=name):
                def peer_script(peer, message=message):
                    self.handshake(peer)
                    if message is None:
                        peer.close()
                    else:
                        peer.send(json.dumps(message) + '\x1e')
                        self.wait_closed(peer)

                client = self.attempt(self.server(peer_script))
                failures = queue.Queue()
                closed = queue.Queue()
                client.on('failure', failures.put)
                client.on('close', lambda closed=closed: closed.put(True))
                client.connect()
                self.assertTrue(closed.get(timeout=2))
                client.stop()
                client.stop()
                self.assertTrue(closed.empty())
                if name == 'signalr_error':
                    failure = failures.get(timeout=2)
                    self.assertIn('Server stopping', str(failure))
                self.assertTrue(failures.empty())

    def test_stop_interrupts_blocked_receive_without_peer_help(self):
        client, peer = self.raw_attempt()
        failures = []
        client.on('failure', failures.append)
        started = time.monotonic()
        client.stop()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(failures, [])
        self.assertNotEqual(peer.fileno(), -1)

    # Upgrade a real socket pair, then stop reading on the peer side
    def raw_attempt(self, client=None):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            listener.listen(1)
            local = socket.create_connection(listener.getsockname())
            peer, _ = listener.accept()
        local.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        peer.settimeout(2)
        self.addCleanup(local.close)
        self.addCleanup(peer.close)
        self.raw_peers.append(peer)
        handshake_errors = queue.Queue()
        if client is None:
            client = self.attempt('ws://localhost/ifcbHub')
        else:
            self.clients.append(client)

        # Registration frames and the JSON handshake fit in short text frames
        def read_message(stream):
            opcode, length = stream.read(2)
            self.assertEqual(opcode, 0x81)
            self.assertTrue(length & 0x80)
            self.assertLess(length & 0x7f, 126)
            mask = stream.read(4)
            data = stream.read(length & 0x7f)
            decoded = bytes(value ^ mask[i % 4]
                            for i, value in enumerate(data))
            self.assertTrue(decoded.endswith(b'\x1e'))
            return json.loads(decoded[:-1])

        def handshake():
            try:
                with peer.makefile('rb') as stream:
                    self.assertIn(b'GET /ifcbHub HTTP/1.1', stream.readline())
                    headers = {}
                    while True:
                        line = stream.readline()
                        if line == b'\r\n':
                            break
                        name, value = line.split(b':', 1)
                        headers[name.lower()] = value.strip()
                    accept = base64.b64encode(hashlib.sha1(
                        headers[b'sec-websocket-key']
                        + b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
                    ).digest())
                    peer.sendall(
                        b'HTTP/1.1 101 Switching Protocols\r\n'
                        b'Upgrade: websocket\r\nConnection: Upgrade\r\n'
                        b'Sec-WebSocket-Accept: ' + accept + b'\r\n\r\n'
                    )

                    self.assertEqual(read_message(stream),
                                     {'protocol': 'json', 'version': 1})
                    peer.sendall(b'\x81\x03{}\x1e')
                    if isinstance(client, IFCBClient):
                        invocation = read_message(stream)
                        self.assertEqual(invocation['target'], 'startAsClient')
                        self.assertEqual(invocation['arguments'], [SERIAL])
                        registered = json.dumps({
                            'type': 1, 'target': 'startedAsClient',
                            'arguments': [SERIAL],
                        }).encode() + b'\x1e'
                        self.assertLess(len(registered), 126)
                        peer.sendall(
                            bytes([0x81, len(registered)]) + registered
                        )
                        initialization = [read_message(stream)
                                          for _ in range(2)]
                        self.assertEqual(
                            [item['arguments'][1] for item in initialization],
                            ['Client connected.', 'refresh'],
                        )
            except Exception as error:
                handshake_errors.put(error)

        handshake_thread = threading.Thread(target=handshake, daemon=True)
        handshake_thread.start()
        self.addCleanup(handshake_thread.join, 2)

        def connect_socket(url, **options):
            return connect(url, sock=local, **options)

        with patch.object(signalr, 'connect', side_effect=connect_socket), \
                patch.object(signalr, 'CLOSE_TIMEOUT', 0.1):
            if isinstance(client, IFCBClient):
                client.connect(wait_until_ready=False)
            else:
                client.connect()
            handshake_thread.join(2)
        self.assertFalse(handshake_thread.is_alive())
        if not handshake_errors.empty():
            raise handshake_errors.get_nowait()
        return client, peer

    # Observe bytes entering the socket before testing cancellation under load
    def blocked_writer(self, client=None, peer=None):
        if client is None:
            client, peer = self.raw_attempt()
        finished = threading.Event()
        failures = []

        def write():
            try:
                client.relay_message_to_host('x' * (8 * 1024 * 1024))
            except Exception as error:
                failures.append(error)
            finally:
                finished.set()

        writer = threading.Thread(target=write, daemon=True)
        self.workers.append(writer)
        writer.start()

        self.assertTrue(peer.recv(1, socket.MSG_PEEK))
        self.assertFalse(finished.wait(0.05), 'socket write did not block')
        return client, peer, writer, finished, failures

    # Both stop paths must finish while the peer remains open and unread
    def stop_blocked_writer(self, peer_close=False):
        client, peer, _, finished, failures = self.blocked_writer()
        if peer_close:
            peer.sendall(b'\x88\x02\x03\xe8')
        stopped = threading.Event()
        stop_errors = []
        closed = []
        client.on('close', lambda: closed.append(True))

        def stop():
            try:
                client.stop()
            except Exception as error:
                stop_errors.append(error)
            finally:
                stopped.set()

        stopper = threading.Thread(target=stop, daemon=True)
        self.workers.append(stopper)
        stopper.start()
        self.assertTrue(stopped.wait(0.5), 'stop remains blocked')
        self.assertTrue(finished.wait(0.5), 'write remains blocked')
        self.assertEqual(stop_errors, [])
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ConnectionClosed)
        self.assertEqual(closed, [True])

    def test_stop_interrupts_blocked_write_without_peer_help(self):
        self.stop_blocked_writer()

    def test_stop_interrupts_write_after_peer_websocket_close(self):
        self.stop_blocked_writer(peer_close=True)

    def test_public_client_recovers_from_heartbeat_expiry_during_send(self):
        accepted = queue.Queue()
        messages = queue.Queue()

        def peer_script(peer):
            self.handshake(peer)
            invocation = json.loads(peer.recv(timeout=2)[:-1])
            self.assertEqual(invocation['target'], 'startAsClient')
            self.assertEqual(invocation['arguments'], [SERIAL])
            peer.send(json.dumps({
                'type': 1, 'target': 'startedAsClient', 'arguments': [SERIAL],
            }) + '\x1e')
            initialization = [json.loads(peer.recv(timeout=2)[:-1])
                              for _ in range(2)]
            self.assertEqual([item['arguments'][1] for item in initialization],
                             ['Client connected.', 'refresh'])
            accepted.put(peer)
            while True:
                invocation = json.loads(peer.recv(timeout=3)[:-1])
                if invocation['type'] == 1:
                    messages.put(invocation['arguments'][1])

        client = IFCBClient(self.server(peer_script), SERIAL)
        ready = queue.Queue()
        restored = threading.Event()
        raw = queue.Queue()
        errors = queue.Queue()
        client.on_error(errors.put)
        client.on_ready(ready.put)
        client.on_reconnect(restored.set)
        client.on_any_message(raw.put)
        with patch.object(connection, 'RETRY_INTERVAL', 0.02):
            _, first = self.raw_attempt(client)
            self.assertFalse(ready.get(timeout=2))
            self.assertTrue(ready.get(timeout=2))
            retired = client._connection.client
            _, _, _, finished, failures = self.blocked_writer(client, first)

            # Expiry must abort the send and clean up before retrying
            with patch.object(connection, 'HEARTBEAT_TIMEOUT', 0.1):
                self.assertFalse(ready.get(timeout=2))
            failure = errors.get(timeout=2)
            self.assertIsInstance(failure, TimeoutError)
            self.assertIn('heartbeat', str(failure))
            self.assertTrue(finished.wait(2), 'retired write remains blocked')
            self.assertEqual(len(failures), 1)
            second = accepted.get(timeout=2)
            self.assertTrue(ready.get(timeout=2))
            self.assertTrue(restored.wait(2))
            self.assertIsNot(client._connection.client, retired)
            self.assertFalse(retired.receiver.is_alive())
            second.send(json.dumps({
                'type': 1, 'target': 'messageRelayed',
                'arguments': [SERIAL, 'host', 3, 'syringetrack:1.5'],
            }) + '\x1e')
            self.assertEqual(raw.get(timeout=2),
                             [SERIAL, 'host', 3, 'syringetrack:1.5'])
            client.relay_message_to_host('sample', timeout=0)
            self.assertEqual(messages.get(timeout=2), 'sample')
            self.assertTrue(messages.empty(), 'retired command was replayed')


if __name__ == '__main__':
    unittest.main()
