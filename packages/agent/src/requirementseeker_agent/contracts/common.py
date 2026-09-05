"""Shared wire types. No I/O or environment configuration is performed here."""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
)

SCHEMA_VERSION = "1.0"
Platform = Literal["bilibili", "douyin"]
Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=256, pattern=r"^\S+$")]
Text = Annotated[str, Field(strict=True, min_length=1, max_length=32000, pattern=r"\S")]
Hash = Annotated[str, Field(strict=True, pattern=r"^[0-9a-f]{64}$")]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
Step = Literal["preprocess", "signals", "cluster", "consensus", "context", "opportunity", "dedup"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, serialize_by_alias=True)


def _timestamp(value: object) -> object:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value
    ):
        raise ValueError("timestamp_must_be_rfc3339_with_timezone")
    return value


def _utc(value: datetime) -> datetime:
    try:
        return value.astimezone(UTC)
    except OverflowError:
        raise ValueError("timestamp_out_of_supported_range") from None


Timestamp = Annotated[AwareDatetime, BeforeValidator(_timestamp), AfterValidator(_utc)]


def _public_url(value: HttpUrl) -> HttpUrl:
    if value.username is not None or value.password is not None:
        raise ValueError("source_url_must_not_include_credentials")
    return value


SourceURL = Annotated[HttpUrl, AfterValidator(_public_url)]


class Versions(Contract):
    rules: Identifier
    prompts: Identifier
    schema_version: Literal["1.0"] = Field(alias="schema")
