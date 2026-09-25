"""
Pith binary protocol.

Request frame
=============

Every request is encoded as:

    +--------+------------+---------+------------+---------+---------+
    |  CMD   |  key_len   |   key   | value_len  |  value  |   ttl   |
    |  1 B   |    4 B     |  N B    |    4 B     |  M B    |  8 B    |
    +--------+------------+---------+------------+---------+---------+

All integer fields use little-endian byte order.

CMD:
    0x01 -> SET
    0x02 -> GET
    0x03 -> DEL

key_len:
    Length of key in bytes.
    Range: 1 ... MAX_KEY_SIZE

key:
    Raw key bytes.

value_len:
    Length of value in bytes.
    Range: 1 ... MAX_VALUE_SIZE

value:
    Raw value bytes.
    GET and DEL must use value_len = 0

ttl:
    Unsigned 64-bit integer

Minimum frame size:
    1 + 4 + 1 + 4 + 0 + 8 = 18 bytes



Parser
======

parse_frame() parses exactly one request from the beginning of a byte buffer.

The buffer may contain:
    - only part of frame;
    - exactly one frame;
    - one complete frame followed by another frame.

ParseResult tells the caller:
    INCOMPLETE -> more bytes are required;
    ERROR      -> the frame is invalid;
    COMPLETE   -> a valid Request was parsed.

consumed contains the number of bytes belonging to the parsed frame,
allowing the caller to remove that frame from the buffer and parse the
next one.



Response frame
==============

    +--------+------------+---------+
    | STATUS | value_len  |  value  |
    |  1 B   |    4 B     |  N B    |
    +--------+------------+---------+

value is present only for a successful GET.
"""

import struct
from dataclasses import dataclass
from enum import Enum, IntEnum

MAX_KEY_SIZE = 4096
MAX_VALUE_SIZE = 64 * 1024 * 1024
MIN_FRAME_SIZE = 18

REQUEST_HEADER_SIZE = 5
RESPONSE_HEADER_SIZE = 5


class Command(IntEnum):
    SET = 0x01
    GET = 0x02
    DEL = 0x03


class Status(IntEnum):
    OK = 0x00
    NOT_FOUND = 0x01
    EXPIRED = 0x02
    ERR = 0x03


class ParseState(Enum):
    INCOMPLETE = 1
    ERROR = 2
    COMPLETE = 3


@dataclass(slots=True)
class Request:
    command: Command
    key: bytes
    value: bytes
    ttl: int


@dataclass(slots=True)
class ParseResult:
    state: ParseState
    request: Request | None = None
    consumed: int = 0


@dataclass(slots=True)
class Response:
    status: Status
    value: bytes = b""


def parse_frame(data: bytes | bytearray) -> ParseResult:
    """
    Parse one Pith request from the beginning of `data`.

    Returns:
        INCOMPLETE - not enough bytes yet
        ERROR      - malformed frame
        COMPLETE   - valid request
    """

    # CMD + key_len
    if len(data) < REQUEST_HEADER_SIZE:
        return ParseResult(ParseState.INCOMPLETE)
    command_raw = data[0]

    try:
        command = Command(command_raw)
    except ValueError:
        return ParseResult(ParseState.ERROR)

    key_len = struct.unpack_from("<I", data, 1)[0]

    if key_len == 0 or key_len > MAX_KEY_SIZE:
        return ParseResult(ParseState.ERROR)

    # key + value_len
    if len(data) < REQUEST_HEADER_SIZE + key_len + 4:
        return ParseResult(ParseState.INCOMPLETE)

    key_start = REQUEST_HEADER_SIZE
    key_end = key_start + key_len

    key = bytes(data[key_start:key_end])

    value_len = struct.unpack_from("<I", data, key_end)[0]

    if value_len > MAX_VALUE_SIZE:
        return ParseResult(ParseState.ERROR)

    # SET must contain a value
    if command is Command.SET and value_len == 0:
        return ParseResult(ParseState.ERROR)

    # GET and DEL must not contain a value
    if command in (Command.GET, Command.DEL) and value_len != 0:
        return ParseResult(ParseState.ERROR)

    # Frame:
    #   CMD        1 byte
    #   key_len    4 bytes
    #   key        key_len bytes
    #   value_len  4 bytes
    #   value      value_len bytes
    #   ttl        8 bytes
    #
    # Frame size:
    # 1 + 4 + key_len + 4 + value_len + 8
    frame_size = 1 + 4 + key_len + 4 + value_len + 8

    if len(data) < frame_size:
        return ParseResult(ParseState.INCOMPLETE)

    value_start = key_end + 4
    value_end = value_start + value_len

    value = bytes(data[value_start:value_end])

    ttl = struct.unpack_from("<Q", data, value_end)[0]

    request = Request(command=command, key=key, value=value, ttl=ttl)

    return ParseResult(state=ParseState.COMPLETE, request=request, consumed=frame_size)


def encode_response(response: Response) -> bytes:
    """
    Encode a Pith response frame.
    """
    if len(response.value) > MAX_VALUE_SIZE:
        raise ValueError("response value is too large")
    if response.status is not Status.OK and response.value:
        raise ValueError("non-OK response cannot contain a value")
    return (
        struct.pack(
            "<BI",
            int(response.status),
            len(response.value),
        )
        + response.value
    )


def encode_request(request: Request) -> bytes:
    """
    Encode a Pith request into a binary frame.
    """
    key_len = len(request.key)
    value_len = len(request.value)

    if key_len == 0 or key_len > MAX_KEY_SIZE:
        raise ValueError("key size is out of range")

    if value_len > MAX_VALUE_SIZE:
        raise ValueError("value size is too large")

    if request.command is Command.SET and value_len == 0:
        raise ValueError("SET requires a value")

    if request.command in (Command.GET, Command.DEL) and value_len != 0:
        raise ValueError("GET and DEL must not contain a value")

    if request.ttl < 0 or request.ttl > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("ttl is out of range")

    return (
        bytes([int(request.command)])
        + struct.pack("<I", key_len)
        + request.key
        + struct.pack("<I", value_len)
        + request.value
        + struct.pack("<Q", request.ttl)
    )
