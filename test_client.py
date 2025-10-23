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

def test_phase_1_3():
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

    if reply_type == NBD_REP_INFO and ack_type == NBD_REP_ACK:
        print("\n✓ Phase 1.3 test passed! Option negotiation complete.")
        print("Connection is now in transmission phase (staying open)...")
    else:
        print("\n✗ Test failed: unexpected reply types")

    sock.close()

if __name__ == '__main__':
    test_phase_1_3()
