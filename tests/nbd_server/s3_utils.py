from botocore.exceptions import ClientError

from nbd_server.models import S3Config
from nbd_server.storage.client import ClientManager


async def cleanup_s3_async(
    client_manager: ClientManager,
    s3_config: S3Config,
    export_name: str | None = None,
    cleanup_locks: bool = True,
) -> None:
    async with client_manager.get_client() as s3:
        try:
            if export_name:
                prefix = f"blocks/{export_name}/"
                response = await s3.list_objects_v2(Bucket=s3_config.bucket, Prefix=prefix)
                if "Contents" in response:
                    objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                    if objects_to_delete:
                        await s3.delete_objects(
                            Bucket=s3_config.bucket, Delete={"Objects": objects_to_delete}
                        )

            if cleanup_locks:
                response = await s3.list_objects_v2(Bucket=s3_config.bucket, Prefix="locks/")
                if "Contents" in response:
                    objects_to_delete = [{"Key": obj["Key"]} for obj in response["Contents"]]
                    if objects_to_delete:
                        await s3.delete_objects(
                            Bucket=s3_config.bucket, Delete={"Objects": objects_to_delete}
                        )
        except ClientError:
            pass
