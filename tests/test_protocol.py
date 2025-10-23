import socket
import unittest

from nbd_server.protocol import recv_exactly


class TestRecvExactly(unittest.TestCase):

    def test_recv_exactly_single_chunk(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b'hello'
            sender.sendall(test_data)

            result = recv_exactly(receiver, 5)

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()

    def test_recv_exactly_empty(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b''
            sender.sendall(test_data)

            result = recv_exactly(receiver, 0)

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()

    def test_recv_exactly_large_data(self):
        sender, receiver = socket.socketpair()

        try:
            test_data = b'The quick brown fox jumps over the lazy dog! 1234567890 @#$%^&*()_+-=[]{}|;:,.<>?/~`'
            sender.sendall(test_data)

            result = recv_exactly(receiver, len(test_data))

            self.assertEqual(result, test_data)
        finally:
            sender.close()
            receiver.close()


if __name__ == '__main__':
    unittest.main()
