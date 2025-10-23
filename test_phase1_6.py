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
NBD_CMD_FLUSH = 3
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


def connect_and_negotiate(export_name=b'test_export'):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(('localhost', 10809))
    print("Connected to server")

    handshake = recv_exactly(sock, 18)
    magic, opt_magic, flags = struct.unpack('>QQH', handshake)

    client_flags = struct.pack('>I', NBD_FLAG_C_FIXED_NEWSTYLE)
    sock.sendall(client_flags)

    option_data = struct.pack('>I', len(export_name)) + export_name + struct.pack('>H', 0)
    option_request = struct.pack('>QII', IHAVEOPT, NBD_OPT_GO, len(option_data))
    sock.sendall(option_request + option_data)

    reply_header = recv_exactly(sock, 20)
    reply_magic, reply_option, reply_type, reply_length = struct.unpack('>QIII', reply_header)

    if reply_type == NBD_REP_INFO:
        info_data = recv_exactly(sock, reply_length)
        ack_header = recv_exactly(sock, 20)
        print("Negotiation complete\n")

    return sock


def send_command(sock, cmd_type, handle, offset, length, data=None):
    request = struct.pack('>IHHQQL',
                         NBD_REQUEST_MAGIC, 0, cmd_type,
                         handle, offset, length)
    sock.sendall(request)
    if data:
        sock.sendall(data)

    reply = recv_exactly(sock, 16)
    reply_magic, error, reply_handle = struct.unpack('>IIQ', reply)

    if cmd_type == NBD_CMD_READ:
        read_data = recv_exactly(sock, length)
        return error, read_data
    else:
        return error, None


def test_flush_operation():
    sock = connect_and_negotiate()
    handle = 1

    print("=== Testing WRITE operation ===")
    test_data = b'flush test data'
    offset = 2000
    error, _ = send_command(sock, NBD_CMD_WRITE, handle, offset, len(test_data), test_data)
    print(f"WRITE: offset={offset}, data='{test_data.decode()}', error={error}")
    handle += 1

    print("\n=== Testing FLUSH operation ===")
    error, _ = send_command(sock, NBD_CMD_FLUSH, handle, 0, 0)
    print(f"FLUSH: error={error}")
    if error == 0:
        print("✅ SUCCESS: FLUSH command accepted!")
    else:
        print(f"❌ FAILURE: FLUSH returned error {error}")
    handle += 1

    print("\n=== Testing READ after FLUSH ===")
    error, read_data = send_command(sock, NBD_CMD_READ, handle, offset, len(test_data))
    print(f"READ: offset={offset}, data='{read_data.decode()}', error={error}")
    if read_data == test_data:
        print("✅ SUCCESS: Data persisted after FLUSH!")
    else:
        print("❌ FAILURE: Data corrupted after FLUSH")
    handle += 1

    print("\n=== Testing multiple WRITE-FLUSH cycles ===")
    for i in range(3):
        data = f"cycle{i}".encode()
        offset = 3000 + (i * 100)
        error, _ = send_command(sock, NBD_CMD_WRITE, handle, offset, len(data), data)
        print(f"  Cycle {i}: WRITE at offset={offset}, error={error}")
        handle += 1

        error, _ = send_command(sock, NBD_CMD_FLUSH, handle, 0, 0)
        print(f"  Cycle {i}: FLUSH error={error}")
        handle += 1

    print("\n=== Verifying all cycles ===")
    all_success = True
    for i in range(3):
        expected_data = f"cycle{i}".encode()
        offset = 3000 + (i * 100)
        error, read_data = send_command(sock, NBD_CMD_READ, handle, offset, len(expected_data))
        if read_data == expected_data:
            print(f"  ✅ Cycle {i}: Data '{read_data.decode()}' verified")
        else:
            print(f"  ❌ Cycle {i}: Expected '{expected_data.decode()}', got '{read_data.decode()}'")
            all_success = False
        handle += 1

    if all_success:
        print("\n✅ SUCCESS: All WRITE-FLUSH cycles verified!")

    send_command(sock, NBD_CMD_DISC, handle, 0, 0)
    sock.close()
    print("\nConnection closed")


if __name__ == '__main__':
    print("Phase 1.6 Test: FLUSH command and InMemoryStorage class\n")
    test_flush_operation()
