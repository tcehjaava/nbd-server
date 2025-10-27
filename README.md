# NBD Server with S3 Backend

Network Block Device (NBD) server implementation with S3-compatible storage backend.

## Quick Start

```bash
make install-local  # Install dependencies and start MinIO
make run            # Run server
```

Run `make help` for all available commands.

## Configuration

Configure via CLI arguments or environment variables with `NBD_` prefix:

```bash
python main.py --help  # View all options
```

For local development:
```bash
cp .env.example .env  # Customize settings
```

## License

MIT License
