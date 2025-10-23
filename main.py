#!/usr/bin/env python3

import logging

from src.nbd_server import NBDServer, InMemoryStorage


def main():
    """Start the NBD server with in-memory storage backend."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create storage backend and server
    storage = InMemoryStorage()
    server = NBDServer(storage)

    # Run the server
    server.run()


if __name__ == '__main__':
    main()
