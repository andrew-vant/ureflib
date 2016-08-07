import codecs
import abc
from abc import abstractmethod
from itertools import product, chain
from pprint import pprint

import bitstring

from . import util


class Field(abc.ABCMeta):
    def __new__(cls, name, bases, dct):
        # If a base class defines a propertymethod shadowing part of the field
        # definition, the propertymethod takes priority and the value in the
        # definition is prepended with an underscore (so the propmethod has
        # access to it in a standard place). Right now this is only useful for
        # unions, which need methods for the type and mod fields (because
        # they're not fixed)
        for base in bases:
            for k in dct.keys():
                if isinstance(getattr(base, k, None), property):
                    dct["_"+k] = dct.pop(k)

        super().__new__(cls, name, bases, dct)


class Value(object, metaclass=Field):
    # Default values
    #
    # The mandatory ones should probably be defined as abstract propertymethods
    # or something similar, but I'm not sure exactly what, or how, or whether
    # it's worth bothering.
    id = None
    label = None
    type = None
    size = None
    order = 0
    mod = None
    display = None
    pointer = None
    comment = ""

    def __init__(self, parent, auto=None, value=None, bs=None, string=None):
        self.data = BitArray(self.size)
        self.parent = parent
        if value is not None:
            self.value = value
        elif bs is not None:
            self.bits = bs
        elif string is not None:
            self.string = string

    @classmethod
    @property
    def bytesize(cls):
        if self.size is None:
            return None
        else:
            return util.divup(cls.size, 8)

    @property
    def bits(self):
        return Bits(self.data)

    @bits.setter
    def bits(self, bs):
        if self.size is not None and len(bs) != self.size:
            msg = "Input size %s != field size %s"
            raise ValueError(msg, len(bs), self.size)
        self.data = BitArray(bs)

    @property
    @abstractmethod
    def value(self):
        raise NotImplementedError

    @value.setter
    @abstractmethod
    def value(self, value):
        raise NotImplementedError


    @property
    @abstractmethod
    def string(self):
        raise NotImplementedError

    @string.setter
    @abstractmethod
    def string(self, s):
        raise NotImplementedError


class Number(Field):
    size = 8
    mod = 0
    display = ""

    @property
    def value(self):
        return getattr(self.data, self.type) + self.mod

    @value.setter
    def value(self, value):
        self.data.overwrite(Bits(**{self.type: value - self.mod}))

    @property
    def string(self):
        fstr = util.int_format_str(self.display, self.size)
        return fstr.format(self.value)

    @string.setter
    def string(self, s):
        self.value = int(s, 0)


class String(Field):
    display = "ascii"

    @property
    def string(self):
        return codecs.decode(self.data.bytes, self.display)

    @string.setter
    def string(self, value):
        data = codecs.encode(value, self.display)
        if self.size is not None:
            bytesize = self.size // 8
            if len(data) > bytesize:
                msg = "String '%s' (%s bytes) too long for field '%s' (%s bytes)"
                raise ValueError(msg, value, len(data), self.name, bytesize)
            if len(data) < bytesize:
                # Pad short strings with spaces
                padbyte = codecs.encode(" ", self.display)
                padding = [padbyte] * (bytesize - len(data))
                data += bytes(padding)
        bs = BitArray(bytes=data)
        assert size is not None or len(bs) == self.size
        self.data = bs

    @property
    def value(self):
        return self.string

    @value.setter
    def value(self, value):
        self.string = value


class Bitfield(Field):
    mod = "msb0"

    @property
    def string(self):
        return util.displaybits(self.value, self.display)

    @string.setter
    def string(self, s):
        self.value = Bits(bin=util.undisplaybits(s, self.display))

    @property
    def value(self):
        bs = self.bits
        if self.mod == "msb0":
            return self.bits
        elif self.mod == "lsb0":
            return util.lbin_reverse(self.bits)
        else:
            msg = "Bit ordering '%s' is not a thing"
            raise NotImplementedError(msg, self.mod)

    @value.setter
    def value(self, value):
        if self.mod == "msb0":
            self.bits = value
        elif self.mod == "lsb0":
            self.bits = util.lbin_reverse(value)
        else:
            msg = "Bit ordering '%s' is not a thing"
            raise NotImplementedError(msg, self.mod)


class Union(Number, String, Bitfield):
    @property
    @abstractmethod
    def type(self):
        return self._type

    @property
    def mod(self):
        # Derived classes may need to override this. The default tries to treat
        # the mod attribute as an integer if the current type is an integer,
        # and tries to treat it as msb0/lsb0 if the current type is a bitfield;
        # otherwise it gives up and returns None. This will work for int/bin
        # unions as long as you don't need to mod both forms.
        if "int" in self.type:
            return util.intify(self._mod, 0)
        elif "bin" in self.type:
            return self._mod if self._mod else "msb0"
        else:
            return None

    @property
    def value(self):
        return basecls(self.realtype).value(self)

    @value.setter
    def value(self, value):
        basecls(self.realtype).value(self, value)

    @property
    def string(self):
        return basecls(self.realtype).string(self)

    @string.setter
    def string(self, s):
        basecls(self.realtype).string(self, s)


def define_field(name, spec):
    spec = fixspec(spec.copy())
    base = basecls(spec['type'])
    cls = type(name, (base,), spec)
    return cls


def basecls(tp):
    if "int" in tp or "float" in tp:
        return Number
    elif "bin" in tp:
        return Bitfield
    elif "str" in tp:
        return String
    elif "union" in tp:
        return Union
    else:
        return Value


def fixspec(spec):
    """ Unstringify any properties from spec that need it"""
    # FIXME: Move this into MetaField.__new__?
    unstring = {
            'size': lambda s: util.tobits(s, 0),
            'order': lambda s: util.intify(s),
            'mod': lambda s: util.intify(s, s)
            }

    # empty strings get stripped out; the parent provides defaults.
    for k, v in spec.copy().items():
        if v == "":
            del(spec[k])

    # Uncast string values get cast
    for key, func in unstring.items():
        if key in spec and isinstance(spec[key], str):
            spec[key] = func(spec[key])
    return spec
