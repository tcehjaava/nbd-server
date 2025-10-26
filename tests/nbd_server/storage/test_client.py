import unittest

from nbd_server.models import S3Config
from nbd_server.storage.client import ClientManager


def create_test_s3_config() -> S3Config:
    return S3Config(
        endpoint_url="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        bucket="nbd-storage",
        region="us-east-1"
    )


class TestClientManager(unittest.IsolatedAsyncioTestCase):

    async def test_get_client_returns_s3_client(self):
        s3_config = create_test_s3_config()
        manager = ClientManager(s3_config)

        async with manager.get_client() as client:
            self.assertIsNotNone(client)
            response = await client.list_buckets()
            self.assertIn('Buckets', response)


if __name__ == "__main__":
    unittest.main()
