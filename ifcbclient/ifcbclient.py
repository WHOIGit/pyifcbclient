import base64
import logging
import os
import traceback

from signalrcore.hub_connection_builder import HubConnectionBuilder

from .protocol import parse_response


class IFCBClient:
    def __init__(self, server, id):
        self.server_url = server
        self.ifcb_id = id

        self.handlers = []

        self.hub_connection = (
            HubConnectionBuilder()
            .with_url(self.server_url)
            .configure_logging(logging.CRITICAL)
            .with_automatic_reconnect(
                {
                    "type": "raw",
                    "keep_alive_interval": 10,
                    "reconnect_interval": 5,
                    "max_attempts": 5,
                }
            )
            .build()
        )

        self.hub_connection.on_open(self.on_connect)
        self.hub_connection.on("heartbeat", self.do_nothing)
        self.hub_connection.on("messageRelayed", self.handle_message)
        self.hub_connection.on("startedAsClient", self.started)
        self.hub_connection.on("disconnect", self.disconnect)

    def connect(self):
        self.hub_connection.start()

    def on_connect(self):
        self.hub_connection.send("startAsClient", [self.ifcb_id])

    def do_nothing(self, response):
        pass

    def started(self, response):
        self.relay_message_to_host("Client connected.")
        self.relay_message_to_host("refresh")

    def disconnect(self):
        self.relay_message_to_host("Client disconnected.")
        self.hub_connection.stop()

    def relay_message_to_host(self, message):
        self.hub_connection.send("relayMessageToHost", [self.ifcb_id, message])

    def handle_message(self, response):
        sender_id, smsgsrc, seqno, msg = response
        try:
            fields = parse_response(msg)
            for pattern, callback in self.handlers:
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
        self.handlers.append((prefix_pattern, callback))
