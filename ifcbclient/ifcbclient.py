import base64
import logging
import os
import traceback

from signalrcore.hub_connection_builder import HubConnectionBuilder

from .protocol import parse_response


class IFCBClient:
    def __init__(self, server, id, autoconnect=True):
        self.server_url = server
        self.ifcb_id = id

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

        if autoconnect:
            self.connect()

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
            print(msg, parse_response(msg))
        except:
            # signalrcore consumes all exceptions, so we need to catch it
            traceback.print_exc()
