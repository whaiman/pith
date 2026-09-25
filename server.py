import socket
from pathlib import Path

import yaml

from proto import (
    Command,
    ParseState,
    Request,
    Response,
    Status,
    encode_response,
    parse_frame,
)
from storage import Storage

_cfg_path = Path(__file__).resolve().parent / "config.yaml"

with open(_cfg_path, "r", encoding="utf-8") as _f:
    _cfg = yaml.safe_load(_f)


class Server:
    def __init__(self):
        self.host: str = _cfg["ip"]
        self.port: int = _cfg["port"]

        self.server_socket: socket.socket | None = None
        self.storage = Storage()

    def run(self) -> None:
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen()

        print(f"Pith listening on {self.host}:{self.port}")

        try:
            while True:
                client_socket, client_address = self.server_socket.accept()
                print(f"Client connected: {client_address}")

                try:
                    self._handle_client(client_socket)
                finally:
                    client_socket.close()
                    print(f"Client disconnected: {client_address}")

        except KeyboardInterrupt:
            print("\nShutting down...")

        finally:
            self.server_socket.close()
            self.server_socket = None

    def _handle_client(self, client_socket: socket.socket) -> None:
        buffer = bytearray()

        while True:
            try:
                data = client_socket.recv(4096)
            except ConnectionResetError:
                return

            if not data:
                return
            buffer.extend(data)

            while buffer:
                result = parse_frame(buffer)

                if result.state is ParseState.INCOMPLETE:
                    break

                if result.state is ParseState.ERROR:
                    self._send_response(client_socket, Response(Status.ERR))
                    return

                request = result.request

                if request is None:
                    self._send_response(
                        client_socket,
                        Response(Status.ERR),
                    )
                    return

                response = self._execute(request)
                self._send_response(client_socket, response)
                del buffer[: result.consumed]

    def _execute(self, request: Request) -> Response:
        if request.command is Command.SET:
            status = self.storage.set(request.key, request.value, request.ttl)
            return Response(status=status)

        if request.command is Command.GET:
            status, value = self.storage.get(request.key)
            return Response(status=status, value=value)
        if request.command is Command.DEL:
            status = self.storage.delete(request.key)
            return Response(status=status)

        return Response(Status.ERR)

    @staticmethod
    def _send_response(client_socket: socket.socket, response: Response) -> None:
        data = encode_response(response)
        client_socket.sendall(data)


if __name__ == "__main__":
    Server().run()
