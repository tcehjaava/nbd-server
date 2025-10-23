#!/usr/bin/env python3

import socket
import struct

NBDMAGIC = 0x4e42444d41474943
IHAVEOPT = 0x49484156454f5054
NBD_FLAG_FIXED_NEWSTYLE = 0x0001

NBD_OPT_ABORT = 0x00000002
NBD_OPT_GO = 0x00000007

NBD_REP_MAGIC = 0x3e889045565a9
NBD_REP_ACK = 0x00000001
NBD_REP_INFO = 0x00000003

NBD_INFO_EXPORT = 0x0000

NBD_FLAG_HAS_FLAGS = 0x0001
NBD_FLAG_SEND_FLUSH = 0x0002
TRANSMISSION_FLAGS = NBD_FLAG_HAS_FLAGS | NBD_FLAG_SEND_FLUSH

NBD_CMD_READ = 0
NBD_CMD_WRITE = 1
NBD_CMD_DISC = 2
NBD_CMD_FLUSH = 3

NBD_SIMPLE_REPLY_MAGIC = 0x67446698
NBD_REQUEST_MAGIC = 0x25609513

EXPORT_SIZE = 1024 * 1024 * 1024

HOST = 'localhost'
PORT = 10809


def send_handshake(client_socket):
    handshake = struct.pack('>QQH', NBDMAGIC, IHAVEOPT, NBD_FLAG_FIXED_NEWSTYLE)
    client_socket.sendall(handshake)
    print(f"  Sent handshake: {len(handshake)} bytes")


def recv_exactly(client_socket, num_bytes):
    data = b''
    while len(data) < num_bytes:
        chunk = client_socket.recv(num_bytes - len(data))
        if not chunk:
            raise ConnectionError("Connection closed while receiving data")
        data += chunk
    return data


def parse_client_flags(client_socket):
    flags_data = recv_exactly(client_socket, 4)
    flags = struct.unpack('>I', flags_data)[0]
    print(f"  Received client flags: 0x{flags:08x}")
    return flags


def parse_option_request(client_socket):
    header = recv_exactly(client_socket, 16)
    magic, option, length = struct.unpack('>QII', header)

    if magic != IHAVEOPT:
        raise ValueError(f"Invalid option magic: 0x{magic:016x}")

    data = b''
    if length > 0:
        data = recv_exactly(client_socket, length)

    print(f"  Received option: 0x{option:08x}, length: {length}")
    return option, data


def send_info_reply(client_socket, option, export_size):
    info_data = struct.pack('>HQH', NBD_INFO_EXPORT, export_size, TRANSMISSION_FLAGS)
    info_length = len(info_data)

    reply_header = struct.pack('>QIII', NBD_REP_MAGIC, option, NBD_REP_INFO, info_length)
    client_socket.sendall(reply_header + info_data)

    size_mb = export_size / (1024 * 1024)
    print(f"  Sent NBD_REP_INFO: size={size_mb:.0f}MB, flags=0x{TRANSMISSION_FLAGS:04x}")


def send_ack_reply(client_socket, option):
    reply = struct.pack('>QIII', NBD_REP_MAGIC, option, NBD_REP_ACK, 0)
    client_socket.sendall(reply)
    print(f"  Sent NBD_REP_ACK for option 0x{option:08x}")


def parse_command(client_socket):
    header = recv_exactly(client_socket, 28)
    magic, flags, cmd_type, handle, offset, length = struct.unpack('>IHHQQL', header)

    if magic != NBD_REQUEST_MAGIC:
        raise ValueError(f"Invalid request magic: 0x{magic:08x}")

    return cmd_type, flags, handle, offset, length


def send_simple_reply(client_socket, error, handle):
    reply = struct.pack('>IIQ', NBD_SIMPLE_REPLY_MAGIC, error, handle)
    client_socket.sendall(reply)


class InMemoryStorage:
    def __init__(self):
        self.data = {}

    def read(self, offset, length):
        result = b''
        for i in range(length):
            byte_offset = offset + i
            if byte_offset in self.data:
                result += self.data[byte_offset]
            else:
                result += b'\x00'
        return result

    def write(self, offset, data):
        for i in range(len(data)):
            self.data[offset + i] = data[i:i+1]

    def flush(self):
        pass


def main():
    storage = InMemoryStorage()
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

                parse_client_flags(client_socket)

                option, data = parse_option_request(client_socket)

                if option == NBD_OPT_GO:
                    export_name_length = struct.unpack('>I', data[:4])[0]
                    export_name = data[4:4+export_name_length].decode('utf-8')
                    print(f"  Export name: '{export_name}'")

                    send_info_reply(client_socket, option, EXPORT_SIZE)
                    send_ack_reply(client_socket, option)
                    print("  Negotiation complete, entering transmission phase")

                    while True:
                        cmd_type, flags, handle, offset, length = parse_command(client_socket)
                        print(f"  Command: type={cmd_type}, offset={offset}, length={length}")

                        if cmd_type == NBD_CMD_READ:
                            data_to_send = storage.read(offset, length)

                            send_simple_reply(client_socket, 0, handle)
                            client_socket.sendall(data_to_send)
                            print(f"  Sent READ reply: {length} bytes")

                        elif cmd_type == NBD_CMD_WRITE:
                            write_data = recv_exactly(client_socket, length)
                            storage.write(offset, write_data)

                            send_simple_reply(client_socket, 0, handle)
                            print(f"  Processed WRITE: {length} bytes at offset {offset}")

                        elif cmd_type == NBD_CMD_FLUSH:
                            storage.flush()

                            send_simple_reply(client_socket, 0, handle)
                            print(f"  Processed FLUSH")

                        elif cmd_type == NBD_CMD_DISC:
                            print("  Client requested disconnect")
                            break

                        else:
                            print(f"  Unsupported command: {cmd_type}")
                            send_simple_reply(client_socket, 1, handle)

                elif option == NBD_OPT_ABORT:
                    print("  Client requested abort")
                    client_socket.close()
                    print("  Connection closed")
                    continue

                else:
                    print(f"  Unsupported option: 0x{option:08x}")
                    client_socket.close()
                    print("  Connection closed")
                    continue

            except Exception as e:
                print(f"  Error: {e}")
                client_socket.close()
                print("  Connection closed")

    except KeyboardInterrupt:
        print("\n\nShutting down server...")
    finally:
        server_socket.close()
        print("Server stopped")


if __name__ == '__main__':
    main()
