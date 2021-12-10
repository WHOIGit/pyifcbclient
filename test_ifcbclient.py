import unittest
import unittest.mock as mock

from ifcbclient import IFCBClient


class TestIFCBClient(unittest.TestCase):
    def setUp(self):
        self.client = IFCBClient("ws://192.168.1.100/ifcbHub", "111-111-111")

        # Replace the hub connection object with a Mock instance so that we
        # never attempt to actually connect to the network.
        self.client.hub_connection = mock.Mock()

    def simulate_message(self, msg):
        # Unused fields during parsing: sender_id, smsgsrc, seqno
        self.client.handle_message([0, 0, 0, msg])


    # This test proves that we've mocked out the hub connection successfully
    def test_mocked_connection(self):
        self.client.connect()
        self.client.hub_connection.start.assert_called()


    def test_pattern_prefix(self):
        callback = mock.Mock()
        self.client.on(("valuechanged",), callback)
        self.simulate_message("valuechanged:acquisition:started")
        callback.assert_called_once()

    def test_pattern_wildcard(self):
        callback = mock.Mock()
        self.client.on(("valuechanged", None, "started"), callback)
        self.simulate_message("valuechanged:acquisition:started")
        callback.assert_called_once()

    def test_pattern_mismatch(self):
        callback = mock.Mock()
        self.client.on(("valuechanged", None, "stopped"), callback)
        self.simulate_message("valuechanged:acquisition:started")
        callback.assert_not_called()

    def test_callback_params(self):
        callback = mock.Mock()
        self.client.on(("syringetrack",), callback)
        self.simulate_message("syringetrack:3.14159")
        callback.assert_called_with("syringetrack", 3.14159)

    def test_unregister_callback(self):
        callback = mock.Mock()
        handler_id = self.client.on(("valuechanged",), callback)
        self.simulate_message("valuechanged:acquisition:started")
        self.client.unregister(handler_id)
        self.simulate_message("valuechanged:acquisition:started")
        callback.assert_called_once()


    def test_parse_triggerrois(self):
        callback = mock.Mock()
        self.client.on(("triggerrois",), callback)
        self.simulate_message("triggerrois:2:0:0:AAAA:5:5:BBBB")
        callback.assert_called_once_with(
            'triggerrois',
            [('0', '0', b'\x00\x00\x00'), ('5', '5', b'\x04\x10A')]
        )
