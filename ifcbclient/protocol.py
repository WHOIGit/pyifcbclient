import base64
import datetime
import json


fixed_length_responses = [
    ("client","reset"),
    ("client","disconnected"),
    ("client","enableroutines"),
    ("client","disableroutines"),
    ("client","disableui"),
    ("client","enableui"),
    ("syringetrack",float),
    ("movevalvestarted",str,str),
    ("movevalvefinished",str),
    ("triggerchanged",int,"processed",int,int,int),
    ("triggerchanged",int,"saved",int,int),
    ("triggerchanged",int,"skipped",int,int,int,int),
    ("triggerchanged",int,"post",int,int),
    ("triggerimage",str),
    ("triggerbigimage",str),
    ("graphchanged",float,float,float,float,int,int,int,float,float,float,float,str),
    ("file","start",str,int,str),
    ("file","end",str),
    ("valuechanged","acquisition","status",str),
    ("valuechanged","acquisition","started"),
    ("valuechanged","acquisition","stopped"),
    ("valuechanged","acquisition","emergency"),
    ("valuechanged","pausestate",bool),
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
    ("valuechanged","switchsendtriggercontent",bool),
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
    ("valuechanged","setbiocidevolume",float),
    ("valuechanged","setbleachrinsevolume",float),
    ("valuechanged","setbleachvolume",float),
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
    ("valuechanged","setfastfactor",float),
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
    ("valuechanged","setsyringezero",int),
    ("valuechanged","setsyringemotorcurrent",int),
    ("valuechanged","promptforfilecomment","start",str),
    ("valuechanged","promptforfilecomment","countdown",int),
    ("valuechanged","promptforfilecomment","end",int),
    ("valuechanged","storevalue",str,str),
    ("error","invaliddaqcommand",str),
    ("error","invaliddaqparameter",str,str),
    ("error","invalidroutine",str),
    ("error","invalidcommand",str),
    ("error","invalidgeneralcommand",str),
    ("error","invalidupdatecommand",str),
    ("error","invalidfilepath",str),
    ("update","check",int),
    ("update","check",int,str,str,str,str,str),
    ("update","downloadprogress",int,int),
    ("update","verify",int,str),
    ("update","apply",int),
    ("update","apply",int,str,str),
]


# Interpret Host booleans without treating every nonempty string as true
def _parse_bool(value):
    value = value.lower()
    if value not in ("true", "false"):
        raise ValueError("Invalid boolean: " + repr(value))
    return value == "true"


def parse_response(m):
    args = m.split(":")

    # Attempt to match the argument list against one of the candidate patterns
    matched = False
    for candidate in fixed_length_responses:
        if candidate[0] != args[0]:
            continue

        # A final text field may contain colons, including paths and comments
        fields = args
        if candidate[-1] is str and len(args) > len(candidate):
            fields = m.split(":", len(candidate) - 1)
        if len(candidate) != len(fields):
            continue

        parsed_args = []
        try:
            for (arg, candarg) in zip(fields, candidate):
                if isinstance(candarg, str):
                    if arg != candarg:
                        break
                    parsed_args.append(arg)
                elif candarg is bool:
                    parsed_args.append(_parse_bool(arg))
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
        groups = json.loads(":".join(args[3:]))
        if groups is None:
            groups = []  # Host serializes an absent routine as null
        parsed_args = args[:3] + groups

    elif args[:2] in (["file", "list"], ["file", "beadslist"]):
        # Combine all arguments into a list
        parsed_args = args[0:2] + [args[2:] if args[2:] != [""] else []]

    elif args[0] in ("triggerrois", "triggerbigrois"):
        rois = []
        count = int(args[1])
        if len(args) != 2 + count * 3:
            raise ValueError("Incorrect number of ROIs")
        for i in range(count):
            left, top = int(args[3*i+2]), int(args[3*i+3])
            image = base64.b64decode(args[3*i+4])
            rois.append((left, top, image))
        parsed_args = [args[0], rois]

    elif args[0] == "triggercontent":
        daq = {}
        ri = args.index("rois")
        for i in range(2, ri-1, 2):
            # Currently, all DAQ values are floating point
            daq[args[i]] = float(args[i+1])

        rois = []
        count = int(args[ri+1])
        for i in range(count):
            left, top = int(args[3*i+ri+2]), int(args[3*i+ri+3])
            image = base64.b64decode(args[3*i+ri+4])
            rois.append((left, top, image))

        parsed_args = ["triggercontent", daq, rois]

    elif args[:2] == ["settings", "list"]:
        count = int(args[2])
        fields = args[3:]
        if count == 0 and fields == [""]:
            fields = []
        if count < 0 or len(fields) != count * 3:
            raise ValueError("Incorrect number of settings")

        # Settings carry their own type codes and escape colons in values
        converters = {"i": int, "d": float, "b": _parse_bool, "s": str}
        settings = []
        for i in range(0, len(fields), 3):
            name, kind, value = fields[i:i + 3]
            if kind not in converters:
                raise ValueError("Unknown setting type: " + repr(kind))
            value = converters[kind](value.replace("{|}", ":"))
            settings.append((name, kind, value))
        parsed_args = args[:2] + [settings]

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
    if parsed_args[0] in ("triggerimage", "triggerbigimage"):
        # Base64 cannot contain the delimiter character, so we're safe
        parsed_args[1] = base64.b64decode(parsed_args[1])

    elif parsed_args[0] == "graphchanged":
        parsed_args[-1] = datetime.datetime.strptime(
            parsed_args[-1], "%Y-%m-%d|%H-%M")

    return parsed_args
