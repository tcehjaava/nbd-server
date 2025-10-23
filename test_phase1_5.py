#!/usr/bin/env python3

import socket
import struct

NBDMAGIC = 0x4e42444d41474943
IHAVEOPT = 0x49484156454f5054
NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001

NBD_OPT_GO = 0x00000007
NBD_REP_MAGIC = 0x3e889045565a9
NBD_REP_ACK = 0x00000001
NBD_REP_INFO = 0x00000003

NBD_CMD_WRITE = 1
NBD_CMD_READ = 0
NBD_CMD_DISC = 2

NBD_REQUEST_MAGIC = 0x25609513
NBD_SIMPLE_REPLY_MAGIC = 0x67446698


def recv_exactly(sock, num_bytes):
    data = b''
    while len(data) < num_bytes:
        chunk = sock.recv(num_bytes - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data += chunk
    return data


def test_write_and_read():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 10809))
    print("Connected to server")

    handshake = recv_exactly(sock, 18)
    magic, opt_magic, flags = struct.unpack('>QQH', handshake)
    print(f"Received handshake: magic=0x{magic:016x}, opt_magic=0x{opt_magic:016x}")

    client_flags = struct.pack('>I', NBD_FLAG_C_FIXED_NEWSTYLE)
    sock.sendall(client_flags)
    print("Sent client flags")

    export_name = b'test_export'
    option_data = struct.pack('>I', len(export_name)) + export_name + struct.pack('>H', 0)
    option_request = struct.pack('>QII', IHAVEOPT, NBD_OPT_GO, len(option_data))
    sock.sendall(option_request + option_data)
    print(f"Sent NBD_OPT_GO for export '{export_name.decode()}'")

    reply_header = recv_exactly(sock, 20)
    reply_magic, reply_option, reply_type, reply_length = struct.unpack('>QIII', reply_header)
    print(f"Received reply: type=0x{reply_type:08x}, length={reply_length}")

    if reply_type == NBD_REP_INFO:
        info_data = recv_exactly(sock, reply_length)
        print(f"Received NBD_REP_INFO")

        ack_header = recv_exactly(sock, 20)
        print("Received NBD_REP_ACK")

    print("\n=== Testing WRITE operation ===")
    test_data = b'hello world!'
    write_offset = 1000
    write_length = len(test_data)
    handle = 1

    write_request = struct.pack('>IHHQQL',
                                NBD_REQUEST_MAGIC, 0, NBD_CMD_WRITE,
                                handle, write_offset, write_length)
    sock.sendall(write_request + test_data)
    print(f"Sent WRITE request: offset={write_offset}, length={write_length}, data='{test_data.decode()}'")

    write_reply = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', write_reply)
    print(f"Received WRITE reply: error={error}")

    print("\n=== Testing READ operation ===")
    read_offset = 1000
    read_length = len(test_data)
    handle = 2

    read_request = struct.pack('>IHHQQL',
                               NBD_REQUEST_MAGIC, 0, NBD_CMD_READ,
                               handle, read_offset, read_length)
    sock.sendall(read_request)
    print(f"Sent READ request: offset={read_offset}, length={read_length}")

    read_reply = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', read_reply)
    read_data = recv_exactly(sock, read_length)
    print(f"Received READ reply: error={error}, data='{read_data.decode()}'")

    if read_data == test_data:
        print("\n✅ SUCCESS: Read data matches written data!")
    else:
        print(f"\n❌ FAILURE: Read data '{read_data}' does not match written data '{test_data}'")

    print("\n=== Testing READ from different offset (should be zeros) ===")
    read_offset = 5000
    read_length = 10
    handle = 3

    read_request = struct.pack('>IHHQQL',
                               NBD_REQUEST_MAGIC, 0, NBD_CMD_READ,
                               handle, read_offset, read_length)
    sock.sendall(read_request)
    print(f"Sent READ request: offset={read_offset}, length={read_length}")

    read_reply = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', read_reply)
    read_data = recv_exactly(sock, read_length)
    print(f"Received READ reply: data (hex)={read_data.hex()}")

    if read_data == b'\x00' * read_length:
        print("✅ SUCCESS: Unwritten data returns zeros!")
    else:
        print("❌ FAILURE: Unwritten data should be zeros")

    disconnect_request = struct.pack('>IHHQQL',
                                    NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC,
                                    4, 0, 0)
    sock.sendall(disconnect_request)
    print("\n=== Sent disconnect request ===")

    sock.close()
    print("Connection closed")


if __name__ == '__main__':
    print("Phase 1.5 Test: WRITE and READ operations\n")
    test_write_and_read()
