from dataclasses import dataclass


@dataclass
class S3Config:
    """S3 connection configuration for async storage backend."""

    endpoint_url: str
    access_key: str
    secret_key: str
    bucket: str
    region: str
