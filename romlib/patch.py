import struct
import codecs


_ips_header = "PATCH"
_ips_footer = "EOF"
_ips_bogo_address = 0x454f46
_ips_rle_threshold = 10  # How many repeats before trying to use RLE?


class PatchFormatError(Exception):
    pass


class PatchValueError(Exception):
    pass


class Patch(object):
    def __init__(self, data={}, rom=None):
        """ Create a Patch.

        data: A dictionary of changes to be made.
        rom: A rom to filter the changes against. Any changes that are
             no-ops will be removed. Note that this is optional and can
             also be done manually with Patch.filter.
        """
        self.changes = data.copy()
        if rom:
            self.filter(rom)

    @classmethod
    def _blockify(cls, changes):
        """ Convert canonical changes to bytes object changes.

        The idea is to merge adjacent changes into a single change object.
        This would normally be done before writing out a patch so the patch
        is not huge. Romlib's internal format only stores individual byte
        changes because bytes objects are harder to merge, filter, etc.
        """

        merged = {}
        block = bytearray()
        start = None
        offset = None
        for o, v in sorted(changes.items()):
            if start is None:  # First entry
                block.append(v)
                start = o
                offset = o
            elif o == offset + 1:  # Adjacent change
                block.append(v)
                offset = o
            else:
                # Nonadjacent change. Store the current change block and start
                # a new one.
                merged[start] = block
                block = bytearray()
                block.append(v)
                start = o
                offset = o
        if start is not None:
            merged[start] = block
        return merged

    @classmethod
    def from_blocks(cls, blocks):
        """ Load an offset-to-bytes-object dictionary. """
        changes = {}
        for start, data in blocks.items():
            for i, d in enumerate(data):
                changes[start+i] = d
        return Patch(changes)

    @classmethod
    def from_ips(cls, f):
        """ Load an ips patch file. """
        # Read and check the header
        header = f.read(5)
        if codecs.decode(header) != _ips_header:
            raise PatchFormatError("Header mismatch reading IPS file.")

        changes = {}
        while(True):
            # Check for EOF marker
            data = f.read(3)
            if data == codecs.encode(_ips_footer):
                break

            # Start reading a record.
            offset = int.from_bytes(data, 'big')
            size = int.from_bytes(f.read(2), 'big')

            # If size is greater than zero, we have a normal record.
            if size > 0:
                for n, byte in enumerate(f.read(size)):
                    changes[offset+n] = byte

            # If size is instead zero, we have an RLE record.
            else:
                rle_size = int.from_bytes(f.read(2), 'big')
                value = f.read(1)[0]
                for i in range(rle_size):
                    changes[offset+i] = value
        return Patch(changes)

    @classmethod
    def from_ipst(cls, f):
        """ Load an ipst patch file. """
        # Skip empty or commented lines.
        f = (line for line in f if not line or not line.startswith("#"))

        header = next(f).rstrip()
        if header != _ips_header:
            raise PatchFormatError("Header mismatch reading IPST file.")

        changes = {}
        for line_number, line in enumerate(f, 2):
            line = line.rstrip()
            # Check for EOF marker
            if line == _ips_footer:
                break

            # Normal records have three parts, RLE records have four.
            parts = line.split(":")
            if len(parts) == 3:
                offset, size, data = parts
                for n, b in enumerate(bytes.fromhex(data)):
                    changes[int(offset)+n] = b
            elif len(parts) == 4:
                offset, size, rle_size, value = map(int, parts, [16]*4)
                for i in range(rle_size):
                    if value > 0xFF:
                        msg = ("Line {}: RLE value {:02X} "
                               "won't fit in one byte.")
                        raise PatchValueError(msg.format(line_number, value))
                    changes[offset+i] = value
            else:
                msg = "Line {}: IPST format error."
                raise PatchFormatError(msg.format(line_number))

        return Patch(changes)

    def _ips_sanitize_changes(self, bogobyte=None):
        """ Check for bogoaddr issues and return merged/fixed changes.

        This is a helper function for writing variants of IPS.

        FIXME: We should split up blocks that include both a RLE-appropriate
        segment and a normal segment. Careful how this interacts with bogoaddr.
        """
        # Merge blocks of changes.
        blocks = self._blockify(self.changes)

        # Deal with bogoaddress issues.
        try:
            data = blocks.pop(_ips_bogo_address)
            bb = bogobyte.to_bytes(1, "big")
            blocks[_ips_bogo_address-1] = bb + data
        except KeyError:
            # Nothing starts at bogoaddr so we're OK.
            pass
        except AttributeError:
            s = ("A change started at 0x454f46 (EOF) "
                 "but a valid bogobyte was not provided.")
            raise PatchValueError(s)
        return blocks

    def to_ips(self, f, bogobyte=None):
        """ Create an ips patch file."""
        blocks = self._ips_sanitize_changes(bogobyte)
        f.write(_ips_header.encode())
        for offset, data in sorted(blocks.items()):
            # Use RLE if we have a long repetition
            if len(data) > 3 and len(set(data)) == 1:
                f.write(offset.to_bytes(3, 'big'))
                f.write(bytes(2))  # Size is zero for RLE
                f.write(len(data).to_bytes(2, 'big'))
                f.write(data[0:1])
            else:
                f.write(offset.to_bytes(3, 'big'))
                f.write(len(data).to_bytes(2, 'big'))
                f.write(data)
        f.write(_ips_footer.encode())

    def to_ipst(self, f, bogobyte=None):
        """ Create an ipst patch file."""
        blocks = self._ips_sanitize_changes(bogobyte)
        print(_ips_header, file=f)
        for offset, data in sorted(blocks.items()):
            # Use RLE if we have a long repetition
            if len(data) > 3 and len(set(data)) == 1:
                fmt = "{:06X}:{:04X}:{:04X}:{:01X}"
                print(fmt.format(offset, 0, len(data), data[0]), file=f)
            else:
                datastr = ''.join('{:02X}'.format(d) for d in data)
                fmt = "{:06X}:{:04X}:{}"
                print(fmt.format(offset, len(data), datastr), file=f)
        print(_ips_footer, file=f)

    def filter(self, rom):
        """ Remove no-ops from the change list.

        This compares the list of changes to the contents of a ROM and
        filters out any data that is already present."""
        def getbyte(f, offset):
            f.seek(offset)
            return f.read(1)[0]

        self.changes = {offset: value for offset, value in self.changes.items()
                        if value != getbyte(rom, offset)}
