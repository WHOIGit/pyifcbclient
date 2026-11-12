import functools
import logging
import socket
import threading
import traceback

from collections import defaultdict
from signalrcore.hub_connection_builder import HubConnectionBuilder

from .connection import ConnectionManager
from .protocol import parse_response


# Keep SignalR transport details local to a single connection attempt
class _Connection:
    def __init__(self, server, serial):
        self.ifcb_id = serial
        self._stopped = False
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._sender = None
        self.failure = None

        # The IFCB manager owns retries; SignalR must never retry independently
        self.hub_connection = (
            HubConnectionBuilder()
            .with_url(server)
            .configure_logging(logging.CRITICAL)
            .build()
        )

    def connect(self):
        self.hub_connection.start()

    def on_connect(self):
        # Serialize commands and keepalive frames without blocking shutdown.
        # SignalR 1.0.2 writes both directly to the same socket.
        with self._state_lock:
            if self._stopped:
                raise ConnectionError('IFCB connection has been stopped')
            if self._sender is None:
                transport = self.hub_connection.transport
                self._sender = transport._client.send
                transport._client.send = self._send_frame

                # SignalR omits keepalive when its native retries are disabled
                transport.connection_checker.start()
        self.hub_connection.send("startAsClient", [self.ifcb_id])

    def _send_frame(self, *args, **kwargs):
        with self._send_lock:
            if self._stopped:
                raise ConnectionError('IFCB connection has been stopped')
            return self._sender(*args, **kwargs)

    def relay_message_to_host(self, message):
        self.hub_connection.send("relayMessageToHost", [self.ifcb_id, message])

    # Stop locally without sending over a possibly broken link
    def stop(self):
        with self._state_lock:
            if self._stopped:
                return
            self._stopped = True
            transport = self.hub_connection.transport
            if transport is None:
                return

        # SignalR 1.0.2 can join a reader still blocked on recv(). Prevent
        # retries and shut down the socket before joining that reader.
        transport.manually_closing = True
        socket_client = getattr(transport, '_client', None)
        sock = getattr(socket_client, 'sock', None)
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # Already closed or never connected

        # Release both threads even if the reader already reported closure
        transport.connection_checker.stop()
        if sock is not None:
            socket_client.dispose()

        # Complete the normal, deduplicated close notification and state change
        transport.on_socket_close()


class IFCBClient:
    def __init__(self, server, id, *, auto_reconnect=True):
        self.server_url = server
        self.ifcb_id = id
        self.auto_reconnect = auto_reconnect
        self.handlers = {}
        self.next_handler_id = 0
        self._callbacks = defaultdict(list)
        self._connection = ConnectionManager(owner=self)

    # Attach persistent handlers to a fresh, guarded connection attempt
    def _make_connection(self):
        manager = self._connection
        connection = _Connection(self.server_url, self.ifcb_id)
        hub = connection.hub_connection
        guard = functools.partial(manager._guard, connection)
        registered = guard(manager._registered)
        closed = guard(manager._disconnect)

        def opened():
            try:
                self.on_connect()
            except Exception as failure:
                manager.failed(connection, failure)

        def started(response):
            if manager.ready:
                return
            try:
                self.started(response)
            except Exception as failure:
                manager.failed(connection, failure)
                return
            registered(response)

        def dispatch(callback, *args):
            manager.dispatch(
                callback, *args, client=connection, active=True
            )

        def message(response):
            dispatch(self.handle_message, response)
            self._notify(connection, 'message', response)

        hub.on_open(guard(functools.partial(dispatch, opened)))
        hub.on('startedAsClient', guard(functools.partial(dispatch, started)))
        hub.on('heartbeat', guard(manager._heartbeat))
        hub.on('messageRelayed', guard(message))
        hub.on('disconnect', closed)
        hub.on_close(closed)

        # A failed hub invocation does not imply a failed transport
        hub.on_error(guard(
            functools.partial(self._notify, connection, 'error')
        ))
        return connection

    # Snapshot observers while scheduling, never while executing user code
    def _notify(self, client, event, *args):
        callbacks = tuple(self._callbacks[event])
        active = event in ('started', 'heartbeat', 'message', 'reconnect')
        active = active or (event == 'ready' and args[0])
        if event == 'heartbeat':
            callbacks = (self.do_nothing, *callbacks)
        if event == 'error' and not callbacks:
            callbacks = (functools.partial(
                logging.getLogger(__name__).warning, 'IFCB error: %s'
            ),)
        for callback in callbacks:
            self._connection.dispatch(
                callback, *args, client=client, active=active
            )

    @property
    def hub_connection(self):
        connection = (
            getattr(self._connection.callback_context, 'client', None)
            or self._connection.client
        )
        return connection.hub_connection if connection is not None else None

    def connect(self, *, background=False):
        self._connection.start(background=background)

    # Preserve the startup hooks used by IFCBClient subclasses
    def on_connect(self):
        self._connection.callback_context.client.on_connect()

    def started(self, response):
        self.relay_message_to_host("Client connected.")
        self.relay_message_to_host("refresh")

    def do_nothing(self, response):
        pass

    def disconnect(self):
        # Repeated cleanup must not send on a stopped connection
        with self._connection.condition:
            if self._connection.stopping:
                return

        try:
            self.relay_message_to_host("Client disconnected.", timeout=0)
        finally:
            self.stop()

    # Stop locally without sending over a possibly broken link
    def stop(self):
        self._connection.close()

    def relay_message_to_host(self, message, timeout=5.0):
        return self._connection.send(message, timeout=timeout)

    def handle_message(self, response):
        sender_id, smsgsrc, seqno, msg = response
        try:
            fields = parse_response(msg)
            for pattern, callback in self.handlers.values():
                client = getattr(self._connection.callback_context,
                                 'client', None)
                if (client is not None
                        and not self._connection.is_current(client)):
                    break
                for field, value in zip(fields, pattern):
                    if value is None:
                        continue
                    elif field != value:
                        break
                else:
                    # Reached if all fields match the pattern
                    callback(*fields)
        except:
            # signalrcore consumes all exceptions, so we need to catch it here
            # to log it
            traceback.print_exc()

    def on(self, prefix_pattern, callback):
        '''
        Registers a callback to be invoked when a message arrives that begins
        with a field sequence that matches the given pattern.

        A pattern is a sequence of constants and None. None matches any value.
        '''
        handler_id = self.next_handler_id
        self.next_handler_id += 1
        self.handlers[handler_id] = (prefix_pattern, callback)
        return handler_id

    # Report current registration readiness, then every change
    def on_ready(self, callback):
        with self._connection.condition:
            self._callbacks['ready'].append(callback)
            self._connection.dispatch(callback, self._connection.ready)

    # Report attempt starts and scheduled retry delays through the dispatcher
    def on_connecting(self, callback):
        self._callbacks['connecting'].append(callback)

    def on_retry(self, callback):
        self._callbacks['retry'].append(callback)

    def on_started(self, callback):
        self._callbacks['started'].append(callback)

    def on_heartbeat(self, callback):
        self._callbacks['heartbeat'].append(callback)

    def on_close(self, callback):
        self._callbacks['close'].append(callback)

    def on_error(self, callback):
        self._callbacks['error'].append(callback)

    def on_reconnect(self, callback):
        self._callbacks['reconnect'].append(callback)

    def on_any_message(self, callback):
        self._callbacks['message'].append(callback)

    def unregister(self, handler_id):
        del self.handlers[handler_id]
