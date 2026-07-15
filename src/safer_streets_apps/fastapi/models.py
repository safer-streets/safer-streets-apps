from typing import Annotated, Any

from fastapi import Query
from pydantic import BaseModel
from safer_streets_core.spatial import SpatialUnit
from safer_streets_core.utils import CrimeType, Force

MonthStr = Annotated[str, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


class CrimeCountsRequest(BaseModel):
    geography: SpatialUnit
    resolution: int | None = None
    force: Force
    categories: list[CrimeType]
    months: tuple[MonthStr, ...]


class FeaturesRequest(BaseModel):
    geography: SpatialUnit
    # if ids not specified, all features are returned (use with care!)
    ids: list[str] | None = None


class GeogLookupRequest(BaseModel):
    geography: SpatialUnit
    # required (only) when geography is H3
    resolution: int | None = None
    ids: list[str]
    target: SpatialUnit


# TODO tighten up these models
DfJson = list[dict[str, Any]]
