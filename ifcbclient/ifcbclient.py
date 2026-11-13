import functools
import logging
import traceback

from collections import defaultdict

from .connection import ConnectionManager
from .protocol import parse_response
from .signalr import SignalRClient


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
        connection = SignalRClient(self.server_url, self.ifcb_id)
        guard = functools.partial(manager._guard, connection)
        registered = guard(manager._registered)
        closed = guard(manager._disconnect)

        def opened():
            try:
                connection.send('startAsClient', [self.ifcb_id])
            except Exception as failure:
                manager.failed(connection, failure)

        def started(response):
            if manager.ready:
                return
            try:
                self.relay_message_to_host("Client connected.")
                self.relay_message_to_host("refresh")
            except Exception as failure:
                manager.failed(connection, failure)
                return
            registered(response)

        def dispatch(callback, *args):
            manager.dispatch(
                callback, *args, client=connection, active=True
            )

        def message(response):

            # Short server notices are followed by the disconnect event
            if len(response) == 2:
                return
            dispatch(self.handle_message, response)
            self._notify(connection, 'message', response)

        connection.on('open', guard(functools.partial(dispatch, opened)))
        connection.on('startedAsClient', guard(
            functools.partial(dispatch, started)
        ))
        connection.on('heartbeat', guard(manager._heartbeat))
        connection.on('messageRelayed', guard(message))
        connection.on('disconnect', closed)
        connection.on('close', closed)
        connection.on('failure', functools.partial(manager.failed, connection))

        # A failed hub invocation does not imply a failed transport
        connection.on('error', guard(
            functools.partial(self._notify, connection, 'error')
        ))
        return connection

    # Snapshot observers while scheduling, never while executing user code
    def _notify(self, client, event, *args):
        callbacks = tuple(self._callbacks[event])
        active = event in ('started', 'heartbeat', 'message', 'reconnect')
        active = active or (event == 'ready' and args[0])
        if event == 'error' and not callbacks:
            callbacks = (functools.partial(
                logging.getLogger(__name__).warning, 'IFCB error: %s'
            ),)
        for callback in callbacks:
            self._connection.dispatch(
                callback, *args, client=client, active=active
            )

    def connect(self, *, wait_until_ready=True):
        self._connection.start(wait_until_ready=wait_until_ready)

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
        except Exception:
            # Report parser and handler errors without dropping the connection
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
