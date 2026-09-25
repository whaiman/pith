import socket
import struct
import time

from proto import MAX_KEY_SIZE, MAX_VALUE_SIZE, Command, Status

HOST = "127.0.0.1"
PORT = 4444


def encode_request(
    command: Command | int,
    key: bytes,
    value: bytes = b"",
    ttl: int = 0,
) -> bytes:
    return (
        bytes([int(command)])
        + struct.pack("<I", len(key))
        + key
        + struct.pack("<I", len(value))
        + value
        + struct.pack("<Q", ttl)
    )


def recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()

    while len(data) < size:
        chunk = sock.recv(size - len(data))

        if not chunk:
            raise ConnectionError("Server closed connection")

        data.extend(chunk)

    return bytes(data)


def recv_response(sock: socket.socket) -> tuple[Status, bytes]:
    header = recv_exact(sock, 5)

    status_raw, value_len = struct.unpack("<BI", header)

    try:
        status = Status(status_raw)
    except ValueError:
        raise AssertionError(f"Unknown status: 0x{status_raw:02x}")

    value = recv_exact(sock, value_len)

    return status, value


def assert_response(
    sock: socket.socket,
    expected_status: Status,
    expected_value: bytes = b"",
) -> None:
    status, value = recv_response(sock)

    assert status is expected_status, f"Expected status {expected_status}, got {status}"

    assert value == expected_value, f"Expected value {expected_value!r}, got {value!r}"


def assert_server_closed(sock: socket.socket) -> None:
    assert sock.recv(1) == b""


def test_basic_operations():
    with socket.create_connection((HOST, PORT)) as sock:
        sock.sendall(
            encode_request(
                Command.SET,
                b"foo",
                b"bar",
                60,
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.GET,
                b"foo",
            )
        )

        assert_response(sock, Status.OK, b"bar")

        sock.sendall(
            encode_request(
                Command.DEL,
                b"foo",
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.GET,
                b"foo",
            )
        )

        assert_response(sock, Status.NOT_FOUND)

    print("basic operations: OK")


def test_persistent_key():
    with socket.create_connection((HOST, PORT)) as sock:
        sock.sendall(
            encode_request(
                Command.SET,
                b"persistent",
                b"value",
                0,
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.GET,
                b"persistent",
            )
        )

        assert_response(sock, Status.OK, b"value")

    print("persistent key: OK")


def test_expiration():
    with socket.create_connection((HOST, PORT)) as sock:
        sock.sendall(
            encode_request(
                Command.SET,
                b"temporary",
                b"value",
                1,
            )
        )

        assert_response(sock, Status.OK)

        time.sleep(1.2)

        sock.sendall(
            encode_request(
                Command.GET,
                b"temporary",
            )
        )

        assert_response(sock, Status.EXPIRED)

        sock.sendall(
            encode_request(
                Command.GET,
                b"temporary",
            )
        )

        assert_response(sock, Status.NOT_FOUND)

    print("expiration: OK")


def test_set_overwrites_existing_value():
    with socket.create_connection((HOST, PORT)) as sock:
        sock.sendall(
            encode_request(
                Command.SET,
                b"same-key",
                b"first",
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.SET,
                b"same-key",
                b"second",
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.GET,
                b"same-key",
            )
        )

        assert_response(sock, Status.OK, b"second")

    print("SET overwrite: OK")


def test_multiple_frames_in_one_send():
    with socket.create_connection((HOST, PORT)) as sock:
        request_1 = encode_request(
            Command.SET,
            b"a",
            b"one",
        )

        request_2 = encode_request(
            Command.SET,
            b"b",
            b"two",
        )

        request_3 = encode_request(
            Command.GET,
            b"a",
        )

        sock.sendall(request_1 + request_2 + request_3)

        assert_response(sock, Status.OK)
        assert_response(sock, Status.OK)
        assert_response(sock, Status.OK, b"one")

    print("multiple frames in one send: OK")


def test_fragmented_frame():
    with socket.create_connection((HOST, PORT)) as sock:
        request = encode_request(
            Command.SET,
            b"fragmented",
            b"hello",
        )

        split = 7

        sock.sendall(request[:split])

        time.sleep(0.1)

        sock.sendall(request[split:])

        assert_response(sock, Status.OK)

    print("fragmented frame: OK")


def test_full_frame_plus_partial_frame():
    with socket.create_connection((HOST, PORT)) as sock:
        first = encode_request(
            Command.SET,
            b"first",
            b"value",
        )

        second = encode_request(
            Command.GET,
            b"second",
        )

        split = 3

        # Full frame A + only part of frame B.
        sock.sendall(first + second[:split])

        # A must be processed immediately.
        assert_response(sock, Status.OK)

        # B is incomplete, so there must be no second response yet.
        sock.settimeout(0.1)

        try:
            data = sock.recv(1)
            assert data == b""
        except socket.timeout:
            pass

        # Complete frame B.
        sock.settimeout(None)
        sock.sendall(second[split:])

        assert_response(sock, Status.NOT_FOUND)

    print("full frame + partial frame: OK")


def test_empty_value_for_set():
    with socket.create_connection((HOST, PORT)) as sock:
        request = encode_request(
            Command.SET,
            b"foo",
            b"",
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("SET with empty value: OK")


def test_value_for_del():
    with socket.create_connection((HOST, PORT)) as sock:
        request = encode_request(
            Command.DEL,
            b"foo",
            b"illegal",
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("DEL with value: OK")


def test_max_key_size():
    with socket.create_connection((HOST, PORT)) as sock:
        key = b"k" * MAX_KEY_SIZE

        sock.sendall(
            encode_request(
                Command.SET,
                key,
                b"x",
            )
        )

        assert_response(sock, Status.OK)

        sock.sendall(
            encode_request(
                Command.GET,
                key,
            )
        )

        assert_response(sock, Status.OK, b"x")

    print("maximum key size: OK")


def test_oversized_key():
    with socket.create_connection((HOST, PORT)) as sock:
        request = bytes([Command.GET]) + struct.pack("<I", MAX_KEY_SIZE + 1)

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("oversized key: OK")


def test_max_value_size():
    with socket.create_connection((HOST, PORT)) as sock:
        key = b"max-value"
        value = b"x" * MAX_VALUE_SIZE

        sock.sendall(
            encode_request(
                Command.SET,
                key,
                value,
            )
        )

        assert_response(sock, Status.OK)

    print("maximum value size: OK")


def test_oversized_value():
    with socket.create_connection((HOST, PORT)) as sock:
        key = b"too-large"
        value_len = MAX_VALUE_SIZE + 1

        # No need to send the actual value.
        # Parser must reject the declared size immediately.
        request = (
            bytes([Command.SET])
            + struct.pack("<I", len(key))
            + key
            + struct.pack("<I", value_len)
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("oversized value: OK")


def test_truncated_frame():
    with socket.create_connection((HOST, PORT)) as sock:
        request = encode_request(
            Command.SET,
            b"foo",
            b"bar",
        )

        # Send only part of the frame.
        sock.sendall(request[:8])

        # Tell the server that no more request bytes will be sent.
        sock.shutdown(socket.SHUT_WR)

        # Server sees EOF while frame is incomplete.
        assert_server_closed(sock)

    print("truncated frame: OK")


def test_invalid_command():
    with socket.create_connection((HOST, PORT)) as sock:
        request = (
            bytes([0xFF])
            + struct.pack("<I", 1)
            + b"a"
            + struct.pack("<I", 0)
            + struct.pack("<Q", 0)
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("invalid command: OK")


def test_empty_key():
    with socket.create_connection((HOST, PORT)) as sock:
        request = (
            bytes([Command.GET])
            + struct.pack("<I", 0)
            + struct.pack("<I", 0)
            + struct.pack("<Q", 0)
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("empty key: OK")


def test_get_with_value():
    with socket.create_connection((HOST, PORT)) as sock:
        request = encode_request(
            Command.GET,
            b"foo",
            b"illegal",
        )

        sock.sendall(request)

        assert_response(sock, Status.ERR)
        assert_server_closed(sock)

    print("GET with value: OK")


def main():
    test_basic_operations()
    test_persistent_key()
    test_expiration()
    test_set_overwrites_existing_value()

    test_multiple_frames_in_one_send()
    test_fragmented_frame()
    test_full_frame_plus_partial_frame()

    test_empty_value_for_set()
    test_value_for_del()

    test_max_key_size()
    test_oversized_key()

    test_max_value_size()
    test_oversized_value()

    test_truncated_frame()

    test_invalid_command()
    test_empty_key()
    test_get_with_value()

    print("\nAll tests passed.")


if __name__ == "__main__":
    main()
