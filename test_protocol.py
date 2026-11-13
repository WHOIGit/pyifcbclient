import unittest

from ifcbclient.protocol import parse_response


class TestProtocol(unittest.TestCase):

    # Compare literal Host messages with their public callback arguments
    def assert_responses(self, cases):
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(parse_response(message), expected)

    def test_value_changes(self):

        # Program.cs events and Daq/Fluids/Routines value changes
        self.assert_responses([
            ('client:disableui', ['client', 'disableui']),
            ('client:enableui', ['client', 'enableui']),
            ('valuechanged:acquisition:emergency',
             ['valuechanged', 'acquisition', 'emergency']),
            ('triggerchanged:42:post:3:2',
             ['triggerchanged', 42, 'post', 3, 2]),
            ('valuechanged:setsyringezero:120',
             ['valuechanged', 'setsyringezero', 120]),
            ('valuechanged:setsyringemotorcurrent:40',
             ['valuechanged', 'setsyringemotorcurrent', 40]),
            ('valuechanged:setfastfactor:0.5',
             ['valuechanged', 'setfastfactor', 0.5]),
            ('valuechanged:promptforfilecomment:start:sample: coastal',
             ['valuechanged', 'promptforfilecomment', 'start',
              'sample: coastal']),
            ('valuechanged:promptforfilecomment:countdown:10',
             ['valuechanged', 'promptforfilecomment', 'countdown', 10]),
            ('valuechanged:promptforfilecomment:end:0',
             ['valuechanged', 'promptforfilecomment', 'end', 0]),
            ('valuechanged:storevalue:Pump1State:False',
             ['valuechanged', 'storevalue', 'Pump1State', 'False']),
        ])

    def test_errors(self):

        # Program.cs command handlers include empty and colon-bearing details
        self.assert_responses([
            ('error:invaliddaqcommand:deliberatelyinvalid',
             ['error', 'invaliddaqcommand', 'deliberatelyinvalid']),
            ('error:invaliddaqparameter:setpmta:not:a:number',
             ['error', 'invaliddaqparameter', 'setpmta', 'not:a:number']),
            ('error:invalidroutine:missing',
             ['error', 'invalidroutine', 'missing']),
            ('error:invalidcommand:', ['error', 'invalidcommand', '']),
            ('error:invalidgeneralcommand:missing',
             ['error', 'invalidgeneralcommand', 'missing']),
            ('error:invalidupdatecommand:missing',
             ['error', 'invalidupdatecommand', 'missing']),
            ('error:invalidfilepath:C:\\data\\missing.hdr',
             ['error', 'invalidfilepath', 'C:\\data\\missing.hdr']),
        ])

    def test_updates(self):

        # Update.cs escapes URL colons before broadcasting update metadata
        self.assert_responses([
            ('update:check:0', ['update', 'check', 0]),
            ('update:check:1:3.0.0:3.1.0:20260101:'
             'https{|}//example.test/update.zip:0123456789abcdef',
             ['update', 'check', 1, '3.0.0', '3.1.0', '20260101',
              'https{|}//example.test/update.zip', '0123456789abcdef']),
            ('update:downloadprogress:1024:4096',
             ['update', 'downloadprogress', 1024, 4096]),
            ('update:verify:1:3.1.0', ['update', 'verify', 1, '3.1.0']),
            ('update:verify:0:offline', ['update', 'verify', 0, 'offline']),
            ('update:apply:0', ['update', 'apply', 0]),
            ('update:apply:2:3.1.0:test:password',
             ['update', 'apply', 2, '3.1.0', 'test:password']),
        ])

    def test_boolean_values(self):

        # Host emits true/false for switches and pause state
        for message, expected in [
            ('valuechanged:switchlaser:false', False),
            ('valuechanged:switchlaser:true', True),
            ('valuechanged:switchsendtriggercontent:false', False),
            ('valuechanged:switchsendtriggercontent:true', True),
            ('valuechanged:pausestate:false', False),
            ('valuechanged:pausestate:true', True),
        ]:
            with self.subTest(message=message):
                self.assertIs(parse_response(message)[-1], expected)

    def test_text_payloads(self):

        # Program.cs preserves full paths, comments, reports, and file chunks
        self.assert_responses([
            ('valuechanged:setdatafolder:C:\\data\\samples',
             ['valuechanged', 'setdatafolder', 'C:\\data\\samples']),
            ('valuechanged:setfilecomment:station: 12',
             ['valuechanged', 'setfilecomment', 'station: 12']),
            ('valuechanged:storevalue:Comment:station: 12',
             ['valuechanged', 'storevalue', 'Comment', 'station: 12']),
            ('reportevent:Temperature: 20\n',
             ['reportevent', 'Temperature: 20\n']),
            ('file:chunk:routine.json:0:{"StepType":"Wait"}',
             ['file', 'chunk', 'routine.json', 0, '{"StepType":"Wait"}']),
        ])

    def test_routines_and_file_lists(self):

        # Program.cs serializes routine lists and joins filtered sample names
        self.assert_responses([
            ('valuechanged:interactive:load:[]',
             ['valuechanged', 'interactive', 'load']),
            ('valuechanged:interactive:load:null',
             ['valuechanged', 'interactive', 'load']),
            ('valuechanged:interactive:load:[{}]',
             ['valuechanged', 'interactive', 'load', {}]),
            ('valuechanged:interactive:load:'
             '[{"Type":"Sequence"},{"Type":"Iteration","Parameters":[2]}]',
             ['valuechanged', 'interactive', 'load',
              {'Type': 'Sequence'}, {'Type': 'Iteration', 'Parameters': [2]}]),
            ('file:list:', ['file', 'list', []]),
            ('file:list:D20260101T010000_IFCB152:D20260101T020000_IFCB152',
             ['file', 'list', ['D20260101T010000_IFCB152',
                               'D20260101T020000_IFCB152']]),
            ('file:beadslist:', ['file', 'beadslist', []]),
            ('file:beadslist:D20260101T030000_IFCB152',
             ['file', 'beadslist', ['D20260101T030000_IFCB152']]),
        ])

    def test_zoom_images_and_empty_triggers(self):

        # Program.cs uses the same x/y/PNG triples for zoom and normal ROIs
        self.assert_responses([
            ('triggerbigimage:AQID', ['triggerbigimage', b'\x01\x02\x03']),
            ('triggerbigrois:2:12:34:AQID:56:78:BAUG',
             ['triggerbigrois', [(12, 34, b'\x01\x02\x03'),
                                 (56, 78, b'\x04\x05\x06')]]),
            ('triggerbigrois:0', ['triggerbigrois', []]),
            ('triggercontent:daq:PeakA:0.25:RunTime:4.5:rois:0',
             ['triggercontent', {'PeakA': 0.25, 'RunTime': 4.5}, []]),
        ])

    def test_typed_settings(self):

        # Settings.List supplies i/d/b/s types; Program.cs escapes colons
        message = (
            'settings:list:5:SamplesToRun:i:12:PMTA:d:0.5:'
            'LaserON:b:False:CameraON:b:True:'
            'DataFolder:s:C{|}\\data\\samples'
        )
        expected = [
            ('SamplesToRun', 'i', 12),
            ('PMTA', 'd', 0.5),
            ('LaserON', 'b', False),
            ('CameraON', 'b', True),
            ('DataFolder', 's', 'C:\\data\\samples'),
        ]
        result = parse_response(message)
        self.assertEqual(result, ['settings', 'list', expected])
        self.assertIs(result[2][2][2], False)
        self.assertIs(result[2][3][2], True)
        self.assert_responses([
            ('settings:list:0:', ['settings', 'list', []]),
            ('settings:list:1:FileComment:s:',
             ['settings', 'list', [('FileComment', 's', '')]]),
        ])

    def test_invalid_settings(self):

        # Reject incomplete setting records and unsupported types or values
        for message in [
            'settings:list:1:LaserON:b',
            'settings:list:0:LaserON:b:False',
            'settings:list:1:LaserON:b:invalid',
            'settings:list:1:LaserON:unknown:False',
        ]:
            with self.subTest(message=message):
                with self.assertRaises(ValueError):
                    parse_response(message)
