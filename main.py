#!/usr/bin/env python3

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from src.nbd_server.legacy import NBDServer, S3Storage
from src.nbd_server.constants import (
    DEFAULT_BLOCK_SIZE,
    DEFAULT_EXPORT_SIZE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_S3_ACCESS_KEY,
    DEFAULT_S3_BUCKET,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_REGION,
    DEFAULT_S3_SECRET_KEY,
    parse_size,
)


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='NBD Server with S3 backend',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Basic usage
  python main.py --export-name my-disk

  # With custom configuration
  python main.py --export-name my-disk --s3-endpoint http://localhost:9000 --size 2GB

Environment Variables:
  All arguments can be set via environment variables with NBD_ prefix:
  NBD_HOST, NBD_PORT, NBD_EXPORT_NAME, NBD_SIZE,
  NBD_S3_ENDPOINT, NBD_S3_ACCESS_KEY, NBD_S3_SECRET_KEY, NBD_S3_BUCKET, NBD_S3_REGION,
  NBD_LOG_LEVEL
        '''
    )

    parser.add_argument(
        '--export-name',
        default=os.getenv('NBD_EXPORT_NAME'),
        required=not os.getenv('NBD_EXPORT_NAME'),
        help='Export name (required if not set via NBD_EXPORT_NAME)'
    )

    parser.add_argument(
        '--host',
        default=os.getenv('NBD_HOST', DEFAULT_HOST),
        help=f'Server host (default: {DEFAULT_HOST})'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=int(os.getenv('NBD_PORT', str(DEFAULT_PORT))),
        help=f'Server port (default: {DEFAULT_PORT})'
    )

    parser.add_argument(
        '--size',
        default=os.getenv('NBD_SIZE', DEFAULT_EXPORT_SIZE),
        help=f'Export size (e.g., 512MB, 1GB, 2TB) (default: {DEFAULT_EXPORT_SIZE})'
    )

    parser.add_argument(
        '--s3-endpoint',
        default=os.getenv('NBD_S3_ENDPOINT', DEFAULT_S3_ENDPOINT),
        help=f'S3 endpoint URL (default: {DEFAULT_S3_ENDPOINT})'
    )

    parser.add_argument(
        '--s3-access-key',
        default=os.getenv('NBD_S3_ACCESS_KEY', DEFAULT_S3_ACCESS_KEY),
        help='S3 access key (default: from env or minioadmin)'
    )

    parser.add_argument(
        '--s3-secret-key',
        default=os.getenv('NBD_S3_SECRET_KEY', DEFAULT_S3_SECRET_KEY),
        help='S3 secret key (default: from env or minioadmin)'
    )

    parser.add_argument(
        '--s3-bucket',
        default=os.getenv('NBD_S3_BUCKET', DEFAULT_S3_BUCKET),
        help=f'S3 bucket name (default: {DEFAULT_S3_BUCKET})'
    )

    parser.add_argument(
        '--s3-region',
        default=os.getenv('NBD_S3_REGION', DEFAULT_S3_REGION),
        help=f'S3 region (default: {DEFAULT_S3_REGION})'
    )

    parser.add_argument(
        '--log-level',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
        default=os.getenv('NBD_LOG_LEVEL', 'INFO'),
        help='Logging level (default: INFO)'
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger = logging.getLogger(__name__)

    try:
        export_size = parse_size(args.size)
        block_size = parse_size(DEFAULT_BLOCK_SIZE)
    except ValueError as e:
        logger.error(f"Error parsing size: {e}")
        sys.exit(1)

    logger.info("Starting NBD Server")
    logger.info("Configuration:")
    logger.info(f"    Host: {args.host}")
    logger.info(f"    Port: {args.port}")
    logger.info(f"    Export name: {args.export_name}")
    logger.info(f"    Export size: {export_size:,} bytes ({args.size})")
    logger.info(f"    Block size: {block_size:,} bytes ({DEFAULT_BLOCK_SIZE})")
    logger.info(f"    S3 endpoint: {args.s3_endpoint}")
    logger.info(f"    S3 bucket: {args.s3_bucket}")
    logger.info(f"    S3 region: {args.s3_region}")

    storage = S3Storage.create(
        export_name=args.export_name,
        endpoint_url=args.s3_endpoint,
        access_key=args.s3_access_key,
        secret_key=args.s3_secret_key,
        bucket=args.s3_bucket,
        region=args.s3_region,
        block_size=block_size,
    )

    server = NBDServer(
        storage=storage,
        host=args.host,
        port=args.port,
        export_size=export_size,
    )

    server.run()


if __name__ == '__main__':
    main()
