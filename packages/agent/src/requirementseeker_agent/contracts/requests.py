"""One-video active snapshot input contract; no business decisions are made."""

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import Field, StrictBool, model_validator

from .common import (
    Contract,
    Identifier,
    NonNegativeInt,
    Platform,
    PositiveInt,
    SourceURL,
    Text,
    Timestamp,
    Versions,
)


class ModelConfiguration(Contract):
    config_ref: Identifier
    model_name: Identifier
    revision: Identifier | None
    supports_text: StrictBool
    supports_images: StrictBool


class Video(Contract):
    platform: Platform
    video_id: Identifier
    author_id: Identifier | None
    title: Text
    description: Annotated[str, Field(strict=True, max_length=32000)]
    source_url: SourceURL
    native_tags: list[Identifier] = Field(max_length=100)


class Comment(Contract):
    platform: Platform
    video_id: Identifier
    comment_id: Identifier
    author_id: Identifier | None
    parent_comment_id: Identifier | None
    text: Text
    first_collected_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def retention_window(self) -> Self:
        if self.expires_at - self.first_collected_at != timedelta(days=10):
            raise ValueError("expiry_must_be_first_collection_plus_ten_days")
        if self.parent_comment_id == self.comment_id:
            raise ValueError("comment_cannot_reply_to_itself")
        return self


class MediaContext(Contract):
    context_id: Identifier
    kind: Literal["subtitle", "transcript", "ocr", "frame"]
    source_ref: Identifier
    text: Text | None
    asset_ref: Identifier | None
    first_collected_at: Timestamp
    expires_at: Timestamp

    @model_validator(mode="after")
    def payload_and_retention(self) -> Self:
        if self.kind == "frame":
            if self.asset_ref is None or self.text is not None:
                raise ValueError("frame_requires_asset_reference_only")
        elif self.text is None or self.asset_ref is not None:
            raise ValueError("text_context_requires_text_only")
        if not timedelta(0) < self.expires_at - self.first_collected_at <= timedelta(days=10):
            raise ValueError("media_retention_must_not_exceed_ten_days")
        return self


class AnalysisBudget(Contract):
    max_comments: PositiveInt = 200
    max_model_calls: NonNegativeInt = 8
    max_input_tokens: NonNegativeInt = 16000
    max_output_tokens: NonNegativeInt = 4000


class RetryPolicy(Contract):
    max_transport_retries: Annotated[int, Field(strict=True, ge=0, le=5)] = 2
    max_output_repairs: Annotated[int, Field(strict=True, ge=0, le=2)] = 1


class AnalysisRequest(Contract):
    schema_version: Literal["1.0"]
    run_id: Identifier
    retry_of_run_id: Identifier | None
    snapshot_id: Identifier
    snapshot_kind: Literal["active_video_snapshot"]
    analysis_time: Timestamp
    versions: Versions
    model: ModelConfiguration
    video: Video
    comments: list[Comment]
    media_context: list[MediaContext] = Field(max_length=100)
    history_revision: Identifier
    budget: AnalysisBudget
    retry_policy: RetryPolicy
    cancellation_requested: StrictBool = False

    @model_validator(mode="after")
    def snapshot_integrity(self) -> Self:
        if self.retry_of_run_id == self.run_id:
            raise ValueError("retry_run_must_have_a_new_id")
        if len(self.comments) > self.budget.max_comments:
            raise ValueError("snapshot_exceeds_comment_budget")
        seen: set[str] = set()
        for comment in self.comments:
            if (comment.platform, comment.video_id) != (self.video.platform, self.video.video_id):
                raise ValueError("snapshot_must_contain_one_platform_video")
            if comment.comment_id in seen:
                raise ValueError("snapshot_contains_duplicate_comment_id")
            seen.add(comment.comment_id)
            if not comment.first_collected_at <= self.analysis_time < comment.expires_at:
                raise ValueError("snapshot_contains_inactive_comment")
        context_ids = [item.context_id for item in self.media_context]
        if len(context_ids) != len(set(context_ids)):
            raise ValueError("duplicate_media_context_id")
        for context in self.media_context:
            if not context.first_collected_at <= self.analysis_time < context.expires_at:
                raise ValueError("snapshot_contains_inactive_media")
        return self
