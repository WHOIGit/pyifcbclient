import base64
import logging
import os
import time

from signalrcore.hub_connection_builder import HubConnectionBuilder


class FileData:
    def __init__(self, fileName, chunkCount, type):
        self.fileName = fileName
        self.chunkCount = chunkCount
        self.type = type
        self.chunks = [None] * self.chunkCount

    def add(self, index, chunk):
        if self.type == "octet/stream":
            base64_bytes = chunk.encode("utf-8")
            decoded_bytes = base64.decodebytes(base64_bytes)
            self.chunks[index] = decoded_bytes
        else:
            self.chunks[index] = chunk

    def save(self, folderToSave):
        with open(
            os.path.join(folderToSave, self.fileName),
            "w" if self.type == "text/plain" else "wb",
        ) as fileToWrite:
            for chunk in self.chunks:
                fileToWrite.write(chunk)


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
        msgType, _, msg = msg.partition(":")

        if msgType == "reportevent":
            print("report: " + msg, end="")
        elif msgType == "triggerchanged":
            triggerParameters = msg.split(":")
            if triggerParameters[1] == "processed":
                print(
                    "Trigger: #"
                    + triggerParameters[0]
                    + " ROIs: "
                    + triggerParameters[2]
                    + " Total ROIs: "
                    + triggerParameters[4]
                )
            elif triggerParameters[1] == "saved":
                print(
                    "Triggers saved: "
                    + triggerParameters[2]
                    + ", Triggers skipped: "
                    + triggerParameters[3]
                )
        elif msgType == "syringetrack":
            print("syringe position: " + msg)
        elif msgType == "movevalvefinished":
            print("Valve moved: " + msg)
        elif msgType == "valuechanged":
            source, _, state = msg.partition(":")
            if source == "fpsrate":
                print("trigger rate: " + "{:.1f}".format(float(state)) + " FPS")
            elif source == "acquisition":
                kind, _, status = state.partition(":")
                if kind == "status":
                    print("running: " + status)
        elif msgType == "file":
            fileMsgType, _, fileStrParameters = msg.partition(":")
            if fileMsgType == "list":
                fileParameters = fileStrParameters.split(":")
                print("file list:")
                for fileParameter in fileParameters:
                    print(fileParameter)
            elif fileMsgType == "start":
                fileName, numChunks, fileType = fileStrParameters.split(":")
                self.downloadFile = FileData(fileName, int(numChunks), fileType)
            elif fileMsgType == "chunk":
                fileName, _, fileStrParameters = \
                    fileStrParameters.partition(":")
                chunkIndex, _, chunk = fileStrParameters.partition(":")
                chunkIndex = int(chunkIndex)
                self.downloadFile.add(chunkIndex, chunk)
            elif fileMsgType == "end":
                self.downloadFile.save("c:\Test")
