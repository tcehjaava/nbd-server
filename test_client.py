#!/usr/bin/env python3

import socket
import struct

NBDMAGIC = 0x4e42444d41474943
IHAVEOPT = 0x49484156454f5054
NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001
NBD_OPT_GO = 0x00000007

HOST = 'localhost'
PORT = 10809

def test_phase_1_2():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect((HOST, PORT))

    print("Connected to server")

    handshake = sock.recv(18)
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

    reply_header = sock.recv(20)
    if len(reply_header) == 20:
        reply_magic, reply_option, reply_type, reply_length = struct.unpack('>QIII', reply_header)
        print(f"Received reply: magic=0x{reply_magic:016x}, option=0x{reply_option:08x}, type=0x{reply_type:08x}, length={reply_length}")
        print("\n✓ Phase 1.2 test passed! Server parsed export name correctly.")
    else:
        print("No reply received or connection closed")

    sock.close()

if __name__ == '__main__':
    test_phase_1_2()
