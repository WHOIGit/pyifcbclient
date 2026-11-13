import itertools
import json
import socket
import threading
import time

from collections import defaultdict

from websockets.exceptions import ConnectionClosedOK
from websockets.sync.client import ClientConnection, connect


CONNECT_TIMEOUT = 10.0
CLOSE_TIMEOUT = 1.0
HANDSHAKE_TIMEOUT = 10.0
KEEPALIVE_INTERVAL = 10.0


# A rejected hub invocation leaves the connection usable
class InvocationError(Exception):
    def __init__(self, message, invocation_id):
        super().__init__(message)
        self.invocation_id = invocation_id


# One JSON SignalR connection; the lifecycle manager owns registration/retries
class SignalRClient:
    def __init__(self, server, serial):
        self.server = server
        self.ifcb_id = serial
        self.failure = None
        self.websocket = None
        self.socket = None
        self.events = defaultdict(list)
        self.stopped = threading.Event()
        self.state_lock = threading.Lock()
        self.receiver = None
        self.pending = ''
        self.invocations = itertools.count(1)

    def on(self, event, callback):
        self.events[event].append(callback)

    def _emit(self, event, *args):
        for callback in tuple(self.events[event]):
            callback(*args)

    # Direct WebSockets skip HTTP negotiation; IFCB uses the JSON hub protocol
    def connect(self):
        try:
            if self.stopped.is_set():
                raise ConnectionError('IFCB connection has been stopped')
            self.websocket = connect(
                self.server,
                open_timeout=CONNECT_TIMEOUT,
                close_timeout=CLOSE_TIMEOUT,
                max_size=None,
                compression=None,
                create_connection=self._create_connection,
            )
            self._send({'protocol': 'json', 'version': 1})
            response = self._read_record(time.monotonic() + HANDSHAKE_TIMEOUT)
            if 'error' in response:
                raise ConnectionError(response['error'])
            if 'type' in response:
                raise ValueError('Expected SignalR handshake response')
            self._emit('open')

            # Serialize worker startup with shutdown, without locking I/O
            with self.state_lock:
                if self.stopped.is_set():
                    raise ConnectionError('IFCB connection has been stopped')
                self.receiver = threading.Thread(
                    target=self._receive, daemon=True
                )
                self.receiver.start()
        except Exception as error:
            if not self.stopped.is_set():
                self._emit('failure', error)
            self.stop()

            # Stop may have run before connect assigned the returned WebSocket
            if self.websocket is not None:
                self.websocket.close()
            raise

    # Retain the supplied socket so shutdown can interrupt a blocked send
    def _create_connection(self, sock, protocol, **kwargs):
        with self.state_lock:
            if self.stopped.is_set():
                raise ConnectionError('IFCB connection has been stopped')
            self.socket = sock
        return ClientConnection(sock, protocol, **kwargs)

    def relay_message_to_host(self, message):
        self.send('relayMessageToHost', [self.ifcb_id, message])

    # Invocation IDs retain command errors; successful results are unused
    def send(self, target, arguments):
        self._send({
            'type': 1,
            'invocationId': str(next(self.invocations)),
            'target': target,
            'arguments': arguments,
        })

    def _send(self, message):
        if self.stopped.is_set():
            raise ConnectionError('IFCB connection has been stopped')
        payload = json.dumps(message, separators=(',', ':')) + '\x1e'
        self.websocket.send(payload)

    # SignalR records may span WebSocket messages or share one message
    def _read_record(self, deadline=None):
        while '\x1e' not in self.pending:
            remaining = None
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('SignalR receive timed out')
            chunk = self.websocket.recv(timeout=remaining)
            if not isinstance(chunk, str):
                raise ValueError('Expected SignalR JSON text')
            self.pending += chunk
        record, self.pending = self.pending.split('\x1e', 1)
        message = json.loads(record)
        if not isinstance(message, dict):
            raise ValueError('Expected a SignalR message object')
        return message

    def _receive(self):
        try:
            next_ping = time.monotonic() + KEEPALIVE_INTERVAL
            while not self.stopped.is_set():

                # SignalR pings share the receive loop's timeout
                if time.monotonic() >= next_ping:
                    self._send({'type': 6})
                    next_ping = time.monotonic() + KEEPALIVE_INTERVAL
                try:
                    message = self._read_record(next_ping)
                except TimeoutError:
                    continue
                kind = message.get('type')
                if kind == 1:
                    self._emit(message['target'], message['arguments'])
                elif kind == 3:
                    if 'error' in message:
                        self._emit('error', InvocationError(
                            message=message['error'],
                            invocation_id=message['invocationId'],
                        ))
                elif kind == 7:
                    if 'error' in message:
                        raise ConnectionError(message['error'])
                    return
                elif kind != 6:
                    raise ValueError('Unsupported SignalR message type')
        except ConnectionClosedOK:
            pass  # Normal peer closure still retires this connection
        except Exception as error:
            if not self.stopped.is_set():
                self._emit('failure', error)
        finally:
            self.stop()

    # Interrupt socket I/O before WebSocket cleanup and joining our receiver
    def stop(self):
        with self.state_lock:
            first = not self.stopped.is_set()
            self.stopped.set()
            receiver = self.receiver
        if first:
            if self.socket is not None:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass  # Already closed or never connected
            if self.websocket is not None:
                self.websocket.close()
            self._emit('close')
        if receiver is not None and receiver is not threading.current_thread():
            receiver.join(timeout=1)
