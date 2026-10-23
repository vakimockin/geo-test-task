import re
from typing import Annotated, NewType

from fastapi import Depends, Header

from geotrack.problems import Unauthenticated

UserId = NewType("UserId", str)

USER_ID_RE = re.compile(r"[A-Za-z0-9_.@-]{1,64}")


def parse_user_id(raw: str | None) -> UserId | None:
    if raw is None or USER_ID_RE.fullmatch(raw) is None:
        return None
    return UserId(raw)


async def caller(
    user_header: Annotated[
        str | None,
        Header(
            alias="X-User-ID",
            description="Caller's user id: 1-64 of A-Z a-z 0-9 _ . @ -",
        ),
    ] = None,
) -> UserId:
    user = parse_user_id(user_header)
    if user is None:
        raise Unauthenticated("send a valid X-User-ID header")
    return user


Caller = Annotated[UserId, Depends(caller)]
