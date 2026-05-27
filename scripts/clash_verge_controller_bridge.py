from __future__ import annotations

import argparse
import selectors
import socket
import socketserver
from pathlib import Path


BUFFER_SIZE = 65536


class UnixBridgeHandler(socketserver.BaseRequestHandler):
    unix_socket_path: str

    def handle(self) -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as upstream:
            upstream.connect(self.unix_socket_path)
            relay(self.request, upstream)


class ThreadingTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def relay(client: socket.socket, upstream: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    selector.register(client, selectors.EVENT_READ, upstream)
    selector.register(upstream, selectors.EVENT_READ, client)
    sockets = (client, upstream)

    try:
        while True:
            for key, _events in selector.select():
                source = key.fileobj
                target = key.data
                data = source.recv(BUFFER_SIZE)
                if not data:
                    return
                target.sendall(data)
    finally:
        selector.close()
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expose Clash Verge's Unix controller socket as a local TCP port.",
    )
    parser.add_argument("--socket", default="/tmp/verge/verge-mihomo.sock")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9097)
    args = parser.parse_args()

    socket_path = Path(args.socket)
    if not socket_path.exists():
        raise SystemExit(f"Unix socket does not exist: {socket_path}")

    handler = type(
        "ConfiguredUnixBridgeHandler",
        (UnixBridgeHandler,),
        {"unix_socket_path": str(socket_path)},
    )
    with ThreadingTcpServer((args.host, args.port), handler) as server:
        print(f"forwarding {args.host}:{args.port} -> {socket_path}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
