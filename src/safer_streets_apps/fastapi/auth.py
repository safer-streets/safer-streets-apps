from hashlib import sha256

from fastapi import HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

api_key = APIKeyHeader(name="x-api-key")

KEY_HASH = "ac446dd018bd0b0810633e24953beaf39435cbf753250f4110a387a0af7a64f3"


async def handle_api_key(_: Request, key: str = Security(api_key)) -> None:
    # just check key hash matches known value
    if sha256(bytes.fromhex(key)).hexdigest() != KEY_HASH:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API key missing or invalid")
