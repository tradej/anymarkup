# -*- coding: utf-8 -*-
import io
import json
import os
import re

import configobj
import six
import xmltodict
import yaml


__all__ = ['AnyMarkupError', 'parse', 'parse_file', 'serialize', 'serialize_file']
__version__ = '0.1.1'


fmt_to_exts = {'ini': ['ini'], 'json': ['json'], 'xml': ['xml'], 'yaml': ['yaml', 'yml']}


class AnyMarkupError(Exception):
    def __init__(self, cause):
        """Wrapper for all errors that occur during anymarkup calls.

        Args:
            cause: either a reraised exception or a string with cause
        """
        super(AnyMarkupError, self).__init__()
        self.cause = cause

    def __str__(self):
        cause = str(self.cause)
        if isinstance(self.cause, Exception):
            cause = 'caught {0}: {1}'.format(type(self.cause), cause)
        return 'AnyMarkupError: {0}'.format(cause)


def parse(inp, format=None, encoding='utf-8'):
    """Parse input from file-like object, unicode string or byte string.

    Args:
        inp: file-like object, unicode string or byte string with the markup
        format: explicitly override the guessed `inp` markup format
        encoding: `inp` encoding, defaults to utf-8
    Returns:
        parsed input (dict or list) containing unicode values
    Raises:
        AnyMarkupError if a problem occurs while parsing or inp
    """
    proper_inp = inp
    if hasattr(inp, 'read'):
        proper_inp = inp.read()
    # if proper_inp is unicode, encode it
    if isinstance(proper_inp, six.text_type):
        proper_inp = proper_inp.encode(encoding)

    # try to guess markup type
    fname = None
    if hasattr(inp, 'name'):
        fname = inp.name
    fmt = _get_format(format, fname, proper_inp)

    # make it look like file-like bytes-yielding object
    proper_inp = six.BytesIO(proper_inp)

    try:
        res = _do_parse(proper_inp, fmt, encoding)
    except Exception as e:
        # I wish there was only Python 3 and I could just use "raise ... from e"
        raise AnyMarkupError(e)
    if res is None:
        res = {}

    return res


def parse_file(path, format=None, encoding='utf-8'):
    """A convenience wrapper of parse, which accepts path of file to parse.

    Args:
        path: path to file to parse
        format: explicitly override the guessed `inp` markup format
        encoding: file encoding, defaults to utf-8
    Returns:
        parsed `inp` (dict or list) containing unicode values
    Raises:
        AnyMarkupError if a problem occurs while parsing
    """
    try:
        return parse(open(path, 'rb'), format, encoding)
    except EnvironmentError as e:
        raise AnyMarkupError(e)


def serialize(struct, format, target=None, encoding='utf-8'):
    """Serialize given structure and return it as encoded string or write it to file-like object.

    Args:
        struct: structure (dict or list) with unicode members to serialize; note that list
            can only be serialized to json
        format: specify markup format to serialize structure as
        target: binary-opened file-like object to serialize to; if None (default),
            the result will be returned instead of writing to `target`
        encoding: encoding to use when serializing, defaults to utf-8
    Returns:
        bytestring with serialized structure if `target` is None; return value of
        `target.write` otherwise
    Raises:
        AnyMarkupError if a problem occurs while serializing
    """
    # raise if "unicode-opened"
    if hasattr(target, 'encoding') and target.encoding:
        raise AnyMarkupError('Input file must be opened in binary mode')

    fname = None
    if hasattr(target, 'name'):
        fname = target.name

    fmt = _get_format(format, fname)
    serialized = _do_serialize(struct, fmt, encoding)
    try:
        if target is None:
            return serialized
        else:
            return target.write(serialized)
    except Exception as e:
        raise AnyMarkupError(e)


def serialize_file(struct, path, format=None, encoding='utf-8'):
    """A convenience wrapper of serialize, which accepts path of file to serialize to.

    Args:
        struct: structure (dict or list) with unicode members to serialize; note that list
            can only be serialized to json
        path: path of the file to serialize to
        format: override markup format to serialize structure as (taken from filename
            by default)
        encoding: encoding to use when serializing, defaults to utf-8
    Returns:
        number of bytes written
    Raises:
        AnyMarkupError if a problem occurs while serializing
    """
    try:
        return serialize(struct, format, open(path, 'wb'), encoding)
    except EnvironmentError as e:
        raise AnyMarkupError(e)


def _do_parse(inp, fmt, encoding):
    """Actually parse input.

    Args:
        inp: bytes yielding file-like object
        fmt: format to use for parsing
        encoding: encoding of `inp`
    Returns:
        parsed `inp` (dict or list) containing unicode values
    Raises:
        various sorts of errors raised by used libraries while parsing
    """
    res = {}

    if fmt == 'ini':
        cfg = configobj.ConfigObj(inp, encoding=encoding)
        # workaround https://github.com/DiffSK/configobj/issues/18#issuecomment-76391689
        res = cfg.dict()
        if six.PY2:
            res = _ensure_unicode_recursive(res, encoding)
    elif fmt == 'json':
        if six.PY3:
            # python 3 json only reads from unicode objects
            inp = io.TextIOWrapper(inp, encoding=encoding)
        res = json.load(inp, encoding=encoding)
    elif fmt == 'xml':
        res = xmltodict.parse(inp, encoding=encoding)
    elif fmt == 'yaml':
        # guesses encoding by its own, there seems to be no way to pass
        #  it explicitly
        res = yaml.safe_load(inp)
        if six.PY2:
            res = _ensure_unicode_recursive(res, encoding)
    else:
        raise  # unknown format

    return res


def _do_serialize(struct, fmt, encoding):
    """Actually serialize input.

    Args:
        struct: structure to serialize to
        fmt: format to serialize to
        encoding: encoding to use while serializing
    Returns:
        encoded serialized structure
    Raises:
        various sorts of errors raised by libraries while serializing
    """
    res = None

    if fmt == 'ini':
        config = configobj.ConfigObj(encoding=encoding)
        for k, v in struct.items():
            config[k] = v
        res = b'\n'.join(config.write())
    elif fmt == 'json':
        # specify separators to get rid of trailing whitespace
        # specify ensure_ascii to make sure unicode is serialized in \x... sequences,
        #  not in \u sequences
        res = json.dumps(struct, indent=2, separators=(',', ': '), ensure_ascii=False).\
                encode(encoding)
    elif fmt == 'xml':
        # passing encoding argument doesn't encode, just sets the xml property
        res = xmltodict.unparse(struct, pretty=True, encoding='utf-8').encode('utf-8')
    elif fmt == 'yaml':
        res = yaml.safe_dump(struct, encoding='utf-8', default_flow_style=False)
    else:
        raise  # unknown format

    return res


def _ensure_unicode_recursive(struct, encoding):
    """A convenience function that recursively makes sure all the strings
    in the structure are decoded unicode. It decodes them if not.

    Args:
        struct: a structure to check and fix
        encoding: encoding to use on found bytestrings
    Returns:
        a fully decoded copy of given structure
    """
    # if it's an empty value
    res = None
    if isinstance(struct, dict):
        res = {}
        for k, v in struct.items():
            res[_ensure_unicode_recursive(k, encoding)] = \
                _ensure_unicode_recursive(v, encoding)
    elif isinstance(struct, list):
        res = []
        for i in struct:
            res.append(_ensure_unicode_recursive(i, encoding))
    elif isinstance(struct, six.binary_type):
        res = struct.decode(encoding)
    elif isinstance(struct, (six.text_type, type(None), type(True))):
        res = struct
    else:
        raise AnyMarkupError('internal error - unexpected type {0} in parsed markup'.
            format(type(struct)))

    return res


def _get_format(format, fname, inp=None):
    """Try to guess markup format of given input.

    Args:
        format: explicit format override to use
        fname: name of file, if a file was used to read `inp`
        inp: optional bytestring to guess format of (can be None, if markup
            format is to be guessed only from `format` and `fname`)
    Returns:
        guessed format (a key of fmt_to_exts dict)
    Raises:
        AnyMarkupError if explicit format override has unsupported value
            or if it's impossible to guess the format
    """
    fmt = None
    err = True

    if format is not None:
        if format in fmt_to_exts:
            fmt = format
            err = False
    elif fname:
        # get file extension without leading dot
        file_ext = os.path.splitext(fname)[1][len(os.path.extsep):]
        for fmt_name, exts in fmt_to_exts.items():
            if file_ext in exts:
                fmt = fmt_name
                err = False

    if fmt is None:
        if inp is not None:
            fmt = _guess_fmt_from_bytes(inp)
            err = False

    if err:
        err_string = 'Failed to guess markup format based on: '
        what = []
        for k, v in {format: 'specified format argument',
                     fname: 'filename', inp: 'input string'}.items():
            if k:
                what.append(v)
        if not what:
            what.append('nothing to guess format from!')
        err_string += ', '.join(what)
        raise AnyMarkupError(err_string)

    return fmt


def _guess_fmt_from_bytes(inp):
    """Try to guess format of given bytestring.

    Args:
        inp: byte string to guess format of
    Returns:
        guessed format
    """
    stripped = inp.strip()
    fmt = None
    ini_section_header_re = re.compile(b'^\[([\w-]+)\]')

    if len(stripped) == 0:
        # this can be anything, so choose json, for example
        fmt = 'yaml'
    else:
        if stripped.startswith(b'<'):
            fmt = 'xml'
        else:
            for l in stripped.splitlines():
                line = l.strip()
                if not line.startswith(b'#') and line:
                    break
            # json, ini or yaml => skip comments and then determine type
            if ini_section_header_re.match(line):
                fmt = 'ini'
            else:
                # we assume that yaml is superset of json
                # TODO: how do we figure out it's not yaml?
                fmt = 'yaml'

    return fmt
