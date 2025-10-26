import asyncio
import struct

import aioboto3
import pytest

from nbd_server.async_server import NBDServer
from nbd_server.models import S3Config
from nbd_server.constants import (
    DEFAULT_S3_ACCESS_KEY,
    DEFAULT_S3_BUCKET,
    DEFAULT_S3_ENDPOINT,
    DEFAULT_S3_REGION,
    DEFAULT_S3_SECRET_KEY,
    IHAVEOPT,
    NBD_CMD_DISC,
    NBD_CMD_FLUSH,
    NBD_CMD_READ,
    NBD_CMD_WRITE,
    NBD_FLAG_FIXED_NEWSTYLE,
    NBD_OPT_GO,
    NBD_REP_ACK,
    NBD_REP_INFO,
    NBD_REP_MAGIC,
    NBD_REQUEST_MAGIC,
    NBD_SIMPLE_REPLY_MAGIC,
    NBDMAGIC,
)

NBD_FLAG_C_FIXED_NEWSTYLE = 0x00000001
DEFAULT_TEST_BLOCK_SIZE = 131072


@pytest.fixture
async def cleanup_s3():
    export_names = ["test-server-export", "disk1", "disk2"]

    async def _cleanup():
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=DEFAULT_S3_ENDPOINT,
            aws_access_key_id=DEFAULT_S3_ACCESS_KEY,
            aws_secret_access_key=DEFAULT_S3_SECRET_KEY,
            region_name=DEFAULT_S3_REGION,
        ) as s3:
            for export_name in export_names:
                prefix = f"blocks/{export_name}/"
                try:
                    response = await s3.list_objects_v2(Bucket=DEFAULT_S3_BUCKET, Prefix=prefix)
                    if "Contents" in response:
                        objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                        await s3.delete_objects(
                            Bucket=DEFAULT_S3_BUCKET, Delete={"Objects": objects_to_delete}
                        )
                except Exception:
                    pass

    await _cleanup()
    yield
    await _cleanup()


def create_server():
    s3_config = S3Config(
        endpoint_url=DEFAULT_S3_ENDPOINT,
        access_key=DEFAULT_S3_ACCESS_KEY,
        secret_key=DEFAULT_S3_SECRET_KEY,
        bucket=DEFAULT_S3_BUCKET,
        region=DEFAULT_S3_REGION,
    )
    return NBDServer(s3_config=s3_config, block_size=DEFAULT_TEST_BLOCK_SIZE)


async def negotiate_and_enter_transmission(reader, writer, export_name="test"):
    handshake = await reader.readexactly(18)
    magic, ihaveopt, flags = struct.unpack(">QQH", handshake)
    assert magic == NBDMAGIC
    assert ihaveopt == IHAVEOPT
    assert flags == NBD_FLAG_FIXED_NEWSTYLE

    writer.write(struct.pack(">I", NBD_FLAG_C_FIXED_NEWSTYLE))
    await writer.drain()

    export_bytes = export_name.encode("utf-8")
    option_data = struct.pack(">I", len(export_bytes)) + export_bytes + struct.pack(">H", 0)
    option_header = struct.pack(">QII", IHAVEOPT, NBD_OPT_GO, len(option_data))
    writer.write(option_header + option_data)
    await writer.drain()

    info_reply = await reader.readexactly(32)
    magic, option, reply_type, length = struct.unpack(">QIII", info_reply[:20])
    assert magic == NBD_REP_MAGIC
    assert option == NBD_OPT_GO
    assert reply_type == NBD_REP_INFO

    ack_reply = await reader.readexactly(20)
    magic, option, reply_type, length = struct.unpack(">QIII", ack_reply)
    assert magic == NBD_REP_MAGIC
    assert option == NBD_OPT_GO
    assert reply_type == NBD_REP_ACK


@pytest.mark.asyncio
async def test_server_handshake(cleanup_s3):
    server = create_server()

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.1)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 10809)

        handshake_data = await reader.readexactly(18)
        assert len(handshake_data) == 18

        magic, ihaveopt, flags = struct.unpack(">QQH", handshake_data)
        assert magic == NBDMAGIC
        assert ihaveopt == IHAVEOPT
        assert flags == NBD_FLAG_FIXED_NEWSTYLE

        writer.close()
        await writer.wait_closed()

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_server_negotiation(cleanup_s3):
    server = create_server()

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.1)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 10809)

        await negotiate_and_enter_transmission(reader, writer, "test-export")

        handle = 99999
        cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_DISC, handle, 0, 0)
        writer.write(cmd)
        await writer.drain()

        writer.close()
        await writer.wait_closed()

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_server_read_write(cleanup_s3):
    server = create_server()

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.1)

    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 10809)
        await negotiate_and_enter_transmission(reader, writer, "test-export")

        test_data = b"hello world"
        handle = 12345
        offset = 0
        length = len(test_data)

        write_cmd = struct.pack(
            ">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_WRITE, handle, offset, length
        )
        writer.write(write_cmd + test_data)
        await writer.drain()

        write_reply = await reader.readexactly(16)
        magic, error, reply_handle = struct.unpack(">IIQ", write_reply)
        assert magic == NBD_SIMPLE_REPLY_MAGIC
        assert error == 0
        assert reply_handle == handle

        read_cmd = struct.pack(">IHHQQL", NBD_REQUEST_MAGIC, 0, NBD_CMD_READ, handle, offset, length)
        writer.write(read_cmd)
        await writer.drain()

        read_reply = await reader.readexactly(16)
        magic, error, reply_handle = struct.unpack(">IIQ", read_reply)
        assert magic == NBD_SIMPLE_REPLY_MAGIC
        assert error == 0

        data = await reader.readexactly(length)
        assert data == test_data

        writer.close()
        await writer.wait_closed()

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_server_storage_pool_sharing(cleanup_s3):
    server = create_server()

    server_task = asyncio.create_task(server.run())
    await asyncio.sleep(0.1)

    try:
        reader1, writer1 = await asyncio.open_connection("127.0.0.1", 10809)
        await negotiate_and_enter_transmission(reader1, writer1, "disk1")

        assert "disk1" in server.storage_pool
        storage1 = server.storage_pool["disk1"]

        reader2, writer2 = await asyncio.open_connection("127.0.0.1", 10809)
        await negotiate_and_enter_transmission(reader2, writer2, "disk1")

        assert len(server.storage_pool) == 1
        storage2 = server.storage_pool["disk1"]
        assert storage1 is storage2

        reader3, writer3 = await asyncio.open_connection("127.0.0.1", 10809)
        await negotiate_and_enter_transmission(reader3, writer3, "disk2")

        assert len(server.storage_pool) == 2
        assert "disk2" in server.storage_pool
        assert server.storage_pool["disk2"] is not storage1

        for writer in [writer1, writer2, writer3]:
            writer.close()
            await writer.wait_closed()

    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass
