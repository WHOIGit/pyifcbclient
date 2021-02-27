# Imaging FlowCytobot Websocket API Client for Python

This repository implements an unofficial API client for the [McLane Research Laboratories Imaging FlowCytobot][ifcb], or IFCB.

[ifcb]: https://mclanelabs.com/imaging-flowcytobot/


## Installation

The latest version of this package can be installed using `pip`:

    pip install https://github.com/WHOIGit/pyifcbclient.git


## Usage

The `IFCBClient` class represents a connection to an IFCB given the URL of its websocket API endpoint and its serial number:

```python
from ifcbclient import IFCBClient
client = IFCBClient("ws://192.168.1.100/ifcbHub", "111-111-111")
```

### Event Handling

Before connecting, it is useful to attach event handlers for different kinds of messages:

```python
# When acquisition is paused or resumed, the IFCB sends one of these messages:
#     valuechanged:pausestate:0  # unpaused
#     valuechanged:pausestate:1  # paused

# Each field is split apart, converted to the native Python type, and passed to
# our handler. Use _ for parameters you don't care about.
def pause_handler(_, _, is_paused):
    print("acquisition is now", "paused" if is_paused else "unpaused")

# Register the handler to be called when one of these messages arrives.
# IFCBClient.on() takes a pattern to match against the message's first few
# fields. None matches any value.
client.on(("valuechanged", "pausestate"), pause_handler)
```

Please consult the IFCB documentation for the message specification.

This module provides only basic support for splitting messages into constituent fields and converting them to Python types. Python `list`s and `tuple`s are synthesized for arguments of some variable-length messages; the list length parameters are dropped. Base64 and JSON fields are decoded, except for `file:chunk` messages where the format is ambiguous. If support for a particular message is missing, please [file a bug][].

[file a bug]: https://github.com/WHOIGit/pyifcbclient/issues/new

If you need to unregister an event handler, use the handler ID returned by the `IFCBClient.on()` method:

```python
handler_id = client.on(("valuechanged", "pausestate"), pause_handler)
client.unregister(handler_id)
```


### Connecting and Disconnecting

Connecting the client starts a background thread for communicating with the IFCB. (Be sure to keep your main thread alive, or the program will terminate.)

```python
client.connect()
client.disconnect()
```


### Issuing Commands

Commands can be sent to the IFCB. Currently, commands must be passed as strings:

```python
client.relay_message_to_host("routine:runsample")
```
