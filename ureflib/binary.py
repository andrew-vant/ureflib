import logging
import os
from bitstring import ConstBitStream
from collections import OrderedDict
from .util import tobits, validate_spec, OrderedDictReader


class RomMap(object):
    def __init__(self, root):
        # Find all the csv files in the structs directory and load them into
        # a dictionary keyed by their base name.
        structfiles = [f for f
                       in os.path.listdir("{}/structs".format(root))
                       if f.endswidth(".csv")]

        self.structs = {}
        for sf in structfiles:
            typename = os.path.splitext(sf)[0]
            struct = RomStruct("{}/structs/{}".format(root, sf))
            self.structs[typename] = struct

        # Now load the array definitions.
        with open("{}/arrays.csv".format(root)) as f:
            self.arrays = [RomArray(OrderedDictReader(f), self.structs)]

class RomSplice(OrderedDict):
    """ Evil bad wrongness.

    This is going to handle entites of the form "field X of entity is the
    value of entry Y in array X.
    """
    pass



class RomArray(OrderedDict):
    requiredproperties = "name", "type", "offset", "length", "stride", "comment"

    def __init__(self, od, structtypes={}):
        super().__init__(od)
        validate_spec(self)
        self['offset'] = tobits(self['offset'])
        self['stride'] = tobits(self['stride'])
        if self['type'] in structtypes:
            self.struct = structtypes[self['type']]
        else:
            raise NotImplementedError("Arrays of primitives not ready yet.")

    def read(self, stream):
        for i in range(self['length']):
            pos = i*self['stride'] + offset
            yield self.struct.read(stream, pos)


class RomStruct(OrderedDict):
    """ Specifies the format of a structure type in a ROM. """
    def __init__(self, filename):
        super().__init__()
        with open(filename, newline="") as f:
            for field in OrderedDictReader(f):
                self[field['id']] = RomStructField(field)

    def read(self, stream, offset = 0):
        """ Read an arbitrary structure from a bitstream.

        The offset is the location in the stream where the structure begins. If
        the stream was created from a file, then it's the offset in the file.
        """
        stream.pos = offset
        od = OrderedDict()
        ordering = {}
        for fid, field in self.items():
            value = stream.read("{}:{}".format(field['type'], field['size']))
            od[fid] = value
            ordering[fid] = field['order']
            if "hex" in field.tags or "pointer" in field.tags:
                bits = field['size']
                # How many bytes is this, rounded up?
                numbytes = bits // 8
                if bits % 8 != 0: # Check for partial bytes
                    numbytes += 1

                digits = numbytes * 2 # Two hex digits per byte
                fmtstr = "0x{{:0{}X}}".format(digits)
                od[fid] = fmtstr.format(value)

        od = OrderedDict(sorted(od.items(), key=lambda item: ordering[item[0]]))
        return od


class RomStructField(OrderedDict):
    """ Specifies the format of a single field of a structure. """
    requiredproperties = "id", "label", "size", "type", "tags", "comment"

    def __init__(self, od):
        super().__init__(od)
        self.tags = {s for s in od['tags'].split("|") if s}
        self['size'] = tobits(self['size'])
        validate_spec(self)

