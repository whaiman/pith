import socket
import struct

from proto import (
    Command,
    Request,
    Status,
    encode_request,
)


class PithClient:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.socket: socket.socket | None = None

    def connect(self) -> None:
        if self.socket is not None:
            raise RuntimeError("Client is already connected")

        self.socket = socket.create_connection((self.host, self.port))

    def close(self) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None

    def set(self, key: bytes, value: bytes, ttl: int = 0) -> Status:
        status, _ = self._request(
            Request(
                command=Command.SET,
                key=key,
                value=value,
                ttl=ttl,
            )
        )

        return status

    def get(self, key: bytes) -> tuple[Status, bytes]:
        return self._request(
            Request(
                command=Command.GET,
                key=key,
            )
        )

    def delete(self, key: bytes) -> Status:
        status, _ = self._request(
            Request(
                command=Command.DEL,
                key=key,
            )
        )

        return status

    def _request(self, request: Request) -> tuple[Status, bytes]:
        if self.socket is None:
            raise RuntimeError("Client is not connected")

        self.socket.sendall(encode_request(request))

        return self._recv_response()

    def _recv_response(self) -> tuple[Status, bytes]:
        header = self._recv_exact(5)

        status_raw, value_len = struct.unpack(
            "<BI",
            header,
        )

        try:
            status = Status(status_raw)
        except ValueError as exc:
            raise ValueError(f"Unknown status: 0x{status_raw:02x}") from exc

        value = self._recv_exact(value_len)

        return status, value

    def _recv_exact(self, size: int) -> bytes:
        if self.socket is None:
            raise RuntimeError("Client is not connected")

        data = bytearray()

        while len(data) < size:
            chunk = self.socket.recv(size - len(data))

            if not chunk:
                raise ConnectionError("Server closed connection")

            data.extend(chunk)

        return bytes(data)

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


def main():
    with PithClient("127.0.0.1", 4444) as client:
        print(
            "SET:",
            client.set(
                b"foo",
                b"bar",
                ttl=60,
            ),
        )

        status, value = client.get(b"foo")

        print(
            "GET:",
            status,
            value,
        )

        print(
            "DEL:",
            client.delete(b"foo"),
        )

        status, value = client.get(b"foo")

        print(
            "GET after DEL:",
            status,
            value,
        )


if __name__ == "__main__":
    main()
