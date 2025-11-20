from hashlib import sha256

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key = APIKeyHeader(name="x-api-key")

KEY_HASH = "a496326a89556f5a09c423657e60ef14a1da7e87e30ba1875662524ac510185f"


async def handle_api_key(_: Request, key: str = Security(api_key)) -> None:
    # just check key hash matches known value
    if sha256(bytes.fromhex(key)).hexdigest() != KEY_HASH:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key missing or invalid")
