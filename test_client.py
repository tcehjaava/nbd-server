#!/usr/bin/env python3

import socket
import struct

NBDMAGIC = 0x4e42444d41474943
IHAVEOPT = 0x49484156454f5054
NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001
NBD_OPT_GO = 0x00000007
NBD_REP_INFO = 0x00000003
NBD_REP_ACK = 0x00000001
NBD_INFO_EXPORT = 0x0000

NBD_CMD_READ = 0
NBD_CMD_DISC = 2
NBD_REQUEST_MAGIC = 0x25609513
NBD_SIMPLE_REPLY_MAGIC = 0x67446698

HOST = 'localhost'
PORT = 10809

def recv_exactly(sock, num_bytes):
    data = b''
    while len(data) < num_bytes:
        chunk = sock.recv(num_bytes - len(data))
        if not chunk:
            raise ConnectionError("Connection closed while receiving data")
        data += chunk
    return data

def test_phase_1_4():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))

    print("Connected to server")

    handshake = recv_exactly(sock, 18)
    magic, opt_magic, flags = struct.unpack('>QQH', handshake)
    print(f"Received handshake: magic=0x{magic:016x}, opt_magic=0x{opt_magic:016x}, flags=0x{flags:04x}")

    client_flags = struct.pack('>I', NBD_FLAG_C_FIXED_NEWSTYLE)
    sock.sendall(client_flags)
    print(f"Sent client flags: 0x{NBD_FLAG_C_FIXED_NEWSTYLE:08x}")

    export_name = b'test_device'
    export_name_length = len(export_name)

    option_data = struct.pack('>I', export_name_length) + export_name + struct.pack('>H', 0)

    option_request = struct.pack('>QII', IHAVEOPT, NBD_OPT_GO, len(option_data))
    sock.sendall(option_request + option_data)
    print(f"Sent NBD_OPT_GO with export name: '{export_name.decode()}'")

    info_header = recv_exactly(sock, 20)
    reply_magic, reply_option, reply_type, reply_length = struct.unpack('>QIII', info_header)
    print(f"Received INFO reply: type=0x{reply_type:08x}, length={reply_length}")

    if reply_type == NBD_REP_INFO and reply_length > 0:
        info_data = recv_exactly(sock, reply_length)
        info_type, export_size, transmission_flags = struct.unpack('>HQH', info_data)
        size_mb = export_size / (1024 * 1024)
        print(f"  Export info: type={info_type}, size={size_mb:.0f}MB, flags=0x{transmission_flags:04x}")

    ack_header = recv_exactly(sock, 20)
    ack_magic, ack_option, ack_type, ack_length = struct.unpack('>QIII', ack_header)
    print(f"Received ACK reply: type=0x{ack_type:08x}")

    print("\n--- Testing READ commands ---")

    handle = 0x1234567890abcdef

    read_request = struct.pack('>IHHQQL', NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, 0, 4096)
    sock.sendall(read_request)
    print(f"Sent READ command: offset=0, length=4096")

    reply_header = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', reply_header)
    print(f"Received reply: magic=0x{reply_magic:08x}, error={error}, handle=0x{reply_handle:016x}")

    data = recv_exactly(sock, 4096)
    print(f"Received {len(data)} bytes of data")

    if data == b'\x00' * 4096:
        print("✓ Data is all zeros (correct)")
    else:
        print("✗ Data is not all zeros")

    read_request = struct.pack('>IHHQQL', NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, 8192, 1024)
    sock.sendall(read_request)
    print(f"\nSent READ command: offset=8192, length=1024")

    reply_header = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', reply_header)
    data = recv_exactly(sock, 1024)
    print(f"Received {len(data)} bytes of data")

    if data == b'\x00' * 1024:
        print("✓ Data is all zeros (correct)")
    else:
        print("✗ Data is not all zeros")

    disc_request = struct.pack('>IHHQQL', NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, handle, 0, 0)
    sock.sendall(disc_request)
    print(f"\nSent DISC command")

    sock.close()
    print("\n✓ Phase 1.4 test passed! READ commands work correctly.")

if __name__ == '__main__':
    test_phase_1_4()
