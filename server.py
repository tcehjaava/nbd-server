#!/usr/bin/env python3

import socket
import struct

NBDMAGIC = 0x4e42444d41474943
IHAVEOPT = 0x49484156454f5054
NBD_FLAG_FIXED_NEWSTYLE = 0x0001

HOST = 'localhost'
PORT = 10809


def send_handshake(client_socket):
    handshake = struct.pack('>QQH', NBDMAGIC, IHAVEOPT, NBD_FLAG_FIXED_NEWSTYLE)
    client_socket.sendall(handshake)
    print(f"  Sent handshake: {len(handshake)} bytes")


def main():
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_socket.bind((HOST, PORT))
    server_socket.listen(1)

    print(f"NBD Server listening on {HOST}:{PORT}")
    print("Waiting for connections... (Press Ctrl+C to stop)")

    try:
        while True:
            client_socket, client_address = server_socket.accept()
            print(f"\nConnection from {client_address}")

            try:
                send_handshake(client_socket)
                print("  Handshake complete, closing connection")

            except Exception as e:
                print(f"  Error during handshake: {e}")

            finally:
                client_socket.close()
                print("  Connection closed")

    except KeyboardInterrupt:
        print("\n\nShutting down server...")
    finally:
        server_socket.close()
        print("Server stopped")


if __name__ == '__main__':
    main()
