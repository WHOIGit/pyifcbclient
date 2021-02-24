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
        if self.type.startswith("octet/stream"):
            base64_bytes = chunk.encode("utf-8")
            decoded_bytes = base64.decodebytes(base64_bytes)
            self.chunks[index] = decoded_bytes
        else:
            self.chunks[index] = chunk

    def save(self, folderToSave):
        with open(
            os.path.join(folderToSave, self.fileName),
            "w" if self.type.startswith("text/plain") else "wb",
        ) as fileToWrite:
            for chunk in self.chunks:
                fileToWrite.write(chunk)


class IFCBClient:
    def do_nothing(self, response):
        pass

    def connect(self, server, id):
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

        self.hub_connection.on("heartbeat", self.do_nothing)
        self.hub_connection.on("messageRelayed", self.handle_message)
        self.hub_connection.on("startedAsClient", self.started)
        self.hub_connection.on("disconnect", self.disconnect)
        self.hub_connection.start()
        time.sleep(2)
        self.hub_connection.send("startAsClient", [self.ifcb_id])

    def started(self, response):
        self.hub_connection.send(
            "relayMessageToHost", [self.ifcb_id, "Client connected."]
        )
        self.hub_connection.send("relayMessageToHost", [self.ifcb_id, "refresh"])

    def disconnect(self):
        self.hub_connection.send(
            "relayMessageToHost", [self.ifcb_id, "Client disconnected."]
        )
        self.hub_connection.stop()

    def relay_message_to_host(self, message):
        self.hub_connection.send("relayMessageToHost", [self.ifcb_id, message])

    def handle_message(self, response):
        id = response[0]
        smsgsrc = response[1]
        index = response[2]
        message = response[3]
        separatorIndex = message.find(":")
        msgType = message[:separatorIndex]
        msg = message[separatorIndex + 1 :]

        if msgType.startswith("reportevent"):
            print("report: " + msg, end="")
        elif msgType.startswith("triggerchanged"):
            triggerParameters = msg.split(":")
            if triggerParameters[1].find("processed") >= 0:
                print(
                    "Trigger: #"
                    + triggerParameters[0]
                    + " ROIs: "
                    + triggerParameters[2]
                    + " Total ROIs: "
                    + triggerParameters[4]
                )
            elif triggerParameters[1].find("saved") >= 0:
                print(
                    "Triggers saved: "
                    + triggerParameters[2]
                    + ", Triggers skipped: "
                    + triggerParameters[3]
                )
        elif msgType.startswith("syringetrack"):
            print("syringe position: " + msg)
        elif msgType.startswith("movevalvefinished"):
            print("Valve moved: " + msg)
        elif msgType.startswith("valuechanged"):
            msgSeparatorIndex = msg.find(":")
            source = msg[:msgSeparatorIndex]
            state = msg[msgSeparatorIndex + 1 :]
            if source.startswith("fpsrate"):
                print("trigger rate: " + "{:.1f}".format(float(state)) + " FPS")
            elif source.startswith("acquisition"):
                acquisitionSeparatorIndex = state.find(":")
                states = [
                    state[:acquisitionSeparatorIndex]
                    if acquisitionSeparatorIndex > 0
                    else state,
                    state[acquisitionSeparatorIndex + 1 :]
                    if acquisitionSeparatorIndex > 0
                    else "",
                ]
                if states[0].startswith("status"):
                    print("running: " + states[1])
        elif msgType.startswith("file"):
            fileSeparatorIndex = msg.find(":")
            fileMsgType = msg[:fileSeparatorIndex]
            fileStrParameters = msg[fileSeparatorIndex + 1 :]
            if fileMsgType.startswith("list"):
                fileParameters = fileStrParameters.split(":")
                print("file list:")
                for fileParameter in fileParameters:
                    print(fileParameter)
            elif fileMsgType.startswith("start"):
                fileParameters = fileStrParameters.split(":")
                self.downloadFile = FileData(
                    fileParameters[0], int(fileParameters[1]), fileParameters[2]
                )
            elif fileMsgType.startswith("chunk"):
                chunkSeparatorIndex = fileStrParameters.find(":")
                fileName = fileStrParameters[:chunkSeparatorIndex]
                fileStrParameters = fileStrParameters[chunkSeparatorIndex + 1 :]
                chunkSeparatorIndex = fileStrParameters.find(":")
                chunkIndex = int(fileStrParameters[:chunkSeparatorIndex])
                chunk = fileStrParameters[chunkSeparatorIndex + 1 :]
                self.downloadFile.add(chunkIndex, chunk)
            elif fileMsgType.startswith("end"):
                self.downloadFile.save("c:\Test")
