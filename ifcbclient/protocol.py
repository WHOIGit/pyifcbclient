import base64
import datetime
import json


fixed_length_responses = [
    ("client","reset"),
    ("client","disconnected"),
    ("client","enableroutines"),
    ("client","disableroutines"),
    ("syringetrack",float),
    ("movevalvestarted",str,str),
    ("movevalvefinished",str),
    ("triggerchanged",int,"processed",int,int,int),
    ("triggerchanged",int,"saved",int,int),
    ("triggerchanged",int,"skipped",int,int,int,int),
    ("triggerimage",str),
    ("graphchanged",float,float,float,float,int,int,int,float,float,float,float,str),
    ("file","start",str,int,str),
    ("file","end",str),
    ("valuechanged","acquisition","status",str),
    ("valuechanged","acquisition","started"),
    ("valuechanged","acquisition","stopped"),
    ("valuechanged","pausestate",bool),
    ("valuechanged","interactive","load",str),
    ("valuechanged","interactive","clear"),
    ("valuechanged","interactive","save"),
    ("valuechanged","interactive","started"),
    ("valuechanged","interactive","stopped"),
    ("valuechanged","switchstirrer",bool),
    ("valuechanged","switchlaser",bool),
    ("valuechanged","switchcamera",bool),
    ("valuechanged","switchpump1",bool),
    ("valuechanged","switchpump2",bool),
    ("valuechanged","switchautostart",bool),
    ("valuechanged","switchautoshutdown",bool),
    ("valuechanged","switchcontinuoustrigger",bool),
    ("valuechanged","switchrefillafterdebubble",bool),
    ("valuechanged","switchdebubblewithsample",bool),
    ("valuechanged","switchprimewithsample",bool),
    ("valuechanged","switchbackflushwithsample",bool),
    ("valuechanged","switchbeads",bool),
    ("valuechanged","switchrunsamplefast",bool),
    ("valuechanged","switchliveenv",bool),
    ("valuechanged","switchflash",bool),
    ("valuechanged","switchpmta",bool),
    ("valuechanged","switchpmtb",bool),
    ("valuechanged","switchaltpmta",bool),
    ("valuechanged","switchaltpmtb",bool),
    ("valuechanged","switchbleachtoexhaust",bool),
    ("valuechanged","switchviewimages",bool),
    ("valuechanged","switchviewroisonly",bool),
    ("valuechanged","switchviewtriggers",bool),
    ("valuechanged","switchviewgraphs",bool),
    ("valuechanged","switchviewactivity",bool),
    ("valuechanged","switchfeaturegeneration",bool),
    ("valuechanged","switchviewfps",bool),
    ("valuechanged","switchviewsyringe",bool),
    ("valuechanged","switchviewvalve",bool),
    ("valuechanged","switchauxpower1",bool),
    ("valuechanged","switchtriggering",bool),
    ("valuechanged","switchblobgeneration",bool),
    ("valuechanged","switchfolderhierarchy",bool),
    ("valuechanged","switchinteractiveautostart",bool),
    ("valuechanged","switchoutputfiles",bool),
    ("valuechanged","switchuvwithsample",bool),
    ("valuechanged","setbiocidevolume",int),
    ("valuechanged","setbleachrinsevolume",int),
    ("valuechanged","setbleachvolume",int),
    ("valuechanged","setcleaninginterval",int),
    ("valuechanged","setcounteralt",int),
    ("valuechanged","setcounterbeads",int),
    ("valuechanged","setcountercleaning",int),
    ("valuechanged","setpmta",float),
    ("valuechanged","settriga",float),
    ("valuechanged","setpmtb",float),
    ("valuechanged","settrigb",float),
    ("valuechanged","setaltconfigint",int),
    ("valuechanged","setflashvoltage",float),
    ("valuechanged","setaltflashvoltage",float),
    ("valuechanged","setpumpvoltage",float),
    ("valuechanged","setaltpmta",float),
    ("valuechanged","setalttriga",float),
    ("valuechanged","setaltpmtb",float),
    ("valuechanged","setalttrigb",float),
    ("valuechanged","setsamplevolume",float),
    ("valuechanged","setaltsamplevolume",float),
    ("valuechanged","setbeadsvolume",float),
    ("valuechanged","setbeadsinterval",int),
    ("valuechanged","setskipvolume",float),
    ("valuechanged","setfastfactor",int),
    ("valuechanged","getenvironment",float,float),
    ("valuechanged","setdatafolder",str),
    ("valuechanged","setflashdelay",float),
    ("valuechanged","setbinarizethreshold",int),
    ("valuechanged","setminimumblobarea",int),
    ("valuechanged","sethumiditythreshold",float),
    ("valuechanged","setxgrowamount",int),
    ("valuechanged","setygrowamount",int),
    ("valuechanged","setminimumgap",int),
    ("valuechanged","setfilecomment",str),
    ("valuechanged","setfirstframetimeout",int),
    ("valuechanged","setprimevolume",float),
    ("valuechanged","setflushvolume",float),
    ("valuechanged","setdebubbleconevolume",float),
    ("valuechanged","setdebubbleneedlevolume",float),
    ("valuechanged","setdebubblerefillvolume",float),
    ("valuechanged","setdebubbleleftvolume",float),
    ("valuechanged","setbleachrinsecount",int),
    ("valuechanged","setcameragain",int),
    ("valuechanged","setsamplestorun",int),
    ("valuechanged","setfocuslargestep",int),
    ("valuechanged","setfocussmallstep",int),
    ("valuechanged","setlaserlargestep",int),
    ("valuechanged","setlasersmallstep",int),
    ("valuechanged","movefocus",int),
    ("valuechanged","movelaser",int),
    ("valuechanged","fpsrate",float),
    ("valuechanged","uvmodulepresent",bool),
    ("valuechanged","switchpromptfilecomment",bool),
    ("valuechanged","setzoomsize",int,int),
    ("valuechanged","switchviewzoom",bool),
    ("valuechanged","hostversion",str),
]


def parse_response(m):
    args = m.split(":")

    # Attempt to match the argument list against one of the candidate patterns
    matched = False
    for candidate in fixed_length_responses:
        if len(candidate) != len(args):
            continue

        parsed_args = []
        try:
            for (arg, candarg) in zip(args, candidate):
                if isinstance(candarg, str):
                    if arg != candarg:
                        break
                    parsed_args.append(arg)
                else:
                    parsed_args.append(candarg(arg))  # attempt type cast
            else:
                # Reached if we didn't break or throw an exception
                matched = True
                break  # do not consider further candidates
        except ValueError:
            # Reached if we threw an exception; try next candidate response
            continue

    # Handle variable-length messages, and those with string parameters which
    # might contain our delimiter character (they must come last)
    if matched:
        pass

    elif args[0] == "reportevent":
        parsed_args = [ args[0], ":".join(args[1:]) ]

    elif args[:2] == ["file", "chunk"]:
        parsed_args = args[:3] + [ int(args[3]), ":".join(args[4:]) ]

    elif args[:3] == ["valuechanged", "interactive", "load"]:
        parsed_args = args[:3] + json.loads(":".join(args[3:]))

    elif args[:2] == ["file", "list"]:
        # Combine all arguments into a list
        parsed_args = args[0:2] + [ args[2:] ]

    elif args[0] == "triggerrois":
        rois = []
        count = int(args[1])
        if len(args) != 2 + count * 3:
            raise ValueError("Incorrect number of ROIs")
        for i in range(count):
            top, left = int(args[3*i+2]), int(args[3*i+3])
            image = base64.b64decode(args[3*i+4])
            rois.append((top, left, image))
        parsed_args = ["triggerrois", rois]

    elif args[0] == "triggercontent":
        daq = {}
        ri = args.index("rois")
        for i in range(2, ri-1, 2):
            # Currently, all DAQ values are floating point
            daq[args[i]] = float(args[i+1])

        rois = []
        count = int(args[ri+1])
        for i in range(count):
            top, left = int(args[3*i+ri+2]), int(args[3*i+ri+3])
            image = base64.b64decode(args[3*i+ri+4])
            rois.append((top, left, image))

        parsed_args = ["triggercontent", daq, rois]

    elif args[:3] == ["valuechanged", "interactive", "listgroups"]:
        # TODO: This complex message type is not handled
        parsed_args = args

    elif args[:3] == ["valuechanged", "interactive", "liststeps"]:
        # TODO: This complex message type is not handled
        parsed_args = args

    elif args[:2] == ["valuechanged", "currentgpsposition"]:
        # TODO: Parse GPS coordinates (or "n/a")
        parsed_args = args[:2] + [ ":".join(args[2:]) ]

    elif args[:2] == ["valuechanged", "samplegpsposition"]:
        # TODO: Parse GPS coordinates (or "n/a")
        parsed_args = args[:2] + [ ":".join(args[2:]) ]

    else:
        # We still haven't matched it, abandon all hope
        raise ValueError("Message does not match known protocol format: "
                         + repr(m))


    # Fix up some special arguments
    if parsed_args[0] == "triggerimage":
        # Base64 cannot contain the delimiter character, so we're safe
        parsed_args[1] = base64.b64decode(parsed_args[1])

    elif parsed_args[0] == "graphchanged":
        parsed_args[-1] = datetime.datetime.strptime(
            parsed_args[-1], "%Y-%m-%d|%H-%M")

    return parsed_args
