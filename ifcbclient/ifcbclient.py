import logging
import socket
import traceback

from signalrcore.hub_connection_builder import HubConnectionBuilder

from .protocol import parse_response


class IFCBClient:
    def __init__(self, server, id, *, auto_reconnect=True):
        self.server_url = server
        self.ifcb_id = id
        self.auto_reconnect = auto_reconnect
        self._stopped = False
        self._close_callbacks = []

        self.handlers = {}
        self.next_handler_id = 0

        # Keep automatic retries optional for callers that manage reconnection
        builder = (
            HubConnectionBuilder()
            .with_url(self.server_url)
            .configure_logging(logging.CRITICAL)
        )
        if auto_reconnect:
            builder.with_automatic_reconnect(
                {
                    "type": "raw",
                    "keep_alive_interval": 10,
                    "reconnect_interval": 5,
                    "max_attempts": 5,
                }
            )
        self.hub_connection = builder.build()

        self.hub_connection.on_open(self.on_connect)
        self.hub_connection.on("heartbeat", self.do_nothing)
        self.hub_connection.on("messageRelayed", self.handle_message)
        self.hub_connection.on("startedAsClient", self.started)
        self.hub_connection.on("disconnect", self._disconnected)
        self.hub_connection.on_close(self._notify_closed)

    def connect(self):
        self._stopped = False
        self.hub_connection.start()

    def on_connect(self):
        # SignalR 1.0.2 only starts keepalive automatically when retries are
        # enabled. Keep idle connections alive in externally managed mode too.
        if not self.auto_reconnect:
            self.hub_connection.transport.connection_checker.start()
        self.hub_connection.send("startAsClient", [self.ifcb_id])

    def do_nothing(self, response):
        pass

    def started(self, response):
        self.relay_message_to_host("Client connected.")
        self.relay_message_to_host("refresh")

    def disconnect(self):
        if self._stopped:
            return
        try:
            self.relay_message_to_host("Client disconnected.")
        finally:
            self.stop()

    # Stop locally without sending over a possibly broken link
    def stop(self):
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

    # Let an external manager handle server disconnects when retries are off
    def _disconnected(self, *args):
        try:
            self._notify_closed(*args)
        finally:
            if self.auto_reconnect:
                self.disconnect()

    def _notify_closed(self, *args):
        for callback in self._close_callbacks:
            callback(*args)

    def relay_message_to_host(self, message):
        self.hub_connection.send("relayMessageToHost", [self.ifcb_id, message])

    def handle_message(self, response):
        sender_id, smsgsrc, seqno, msg = response
        try:
            fields = parse_response(msg)
            for pattern, callback in self.handlers.values():
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

    def on_started(self, callback):
        self.hub_connection.on('startedAsClient', callback)

    def on_heartbeat(self, callback):
        self.hub_connection.on('heartbeat', callback)

    def on_close(self, callback):
        self._close_callbacks.append(callback)

    def on_error(self, callback):
        self.hub_connection.on_error(callback)

    def on_reconnect(self, callback):
        self.hub_connection.on_reconnect(callback)

    def on_any_message(self, callback):
        self.hub_connection.on('messageRelayed', callback)

    def unregister(self, handler_id):
        del self.handlers[handler_id]
