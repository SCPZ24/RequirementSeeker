"""Versioned sampling metadata kept separate from the AnalysisRequest v1 wire contract."""

from typing import Literal, Self

from pydantic import Field, model_validator

from .common import (
    Contract,
    Identifier,
    NonNegativeInt,
    Platform,
    PositiveInt,
    Text,
    Timestamp,
)

SamplingStratum = Literal["top", "recent", "replies", "long_tail"]
VideoDirection = Literal[
    "software_tool",
    "tutorial_workflow",
    "life_service",
    "ecommerce_marketing",
    "entertainment_culture",
    "unknown",
]


class VideoMetrics(Contract):
    """Optional secondary observations; none of these bypasses the comment-quality gate."""

    views: NonNegativeInt | None = None
    likes: NonNegativeInt | None = None
    favorites: NonNegativeInt | None = None
    shares: NonNegativeInt | None = None
    author_followers: NonNegativeInt | None = None
    production_observation: Text | None = None


class SamplingManifest(Contract):
    """Describe how one video's candidate comment pool was collected."""

    sampling_schema_version: Literal["1.0"]
    manifest_id: Identifier
    platform: Platform
    video_id: Identifier
    captured_at: Timestamp
    reported_total: NonNegativeInt | None
    collection_target: PositiveInt
    collected_total: NonNegativeInt
    pages_requested: PositiveInt
    pages_succeeded: NonNegativeInt
    available_strata: set[SamplingStratum] = Field(min_length=1, max_length=4)
    direction: VideoDirection
    author_id_present: NonNegativeInt
    distinct_author_count: NonNegativeInt
    exact_duplicate_count: NonNegativeInt
    normalized_duplicate_count: NonNegativeInt
    video_metrics: VideoMetrics | None
    candidate_comment_ids: list[Identifier]
    stratum_comment_ids: dict[SamplingStratum, list[Identifier]]

    @model_validator(mode="after")
    def integrity(self) -> Self:
        if self.pages_succeeded > self.pages_requested:
            raise ValueError("successful_pages_exceed_requested_pages")
        if self.author_id_present > self.collected_total:
            raise ValueError("author_count_exceeds_collected_total")
        if self.distinct_author_count > self.author_id_present:
            raise ValueError("distinct_author_count_exceeds_known_authors")
        duplicate_count = self.exact_duplicate_count + self.normalized_duplicate_count
        if duplicate_count > self.collected_total:
            raise ValueError("duplicate_count_exceeds_collected_total")
        if len(self.candidate_comment_ids) != len(set(self.candidate_comment_ids)):
            raise ValueError("candidate_comment_ids_must_be_unique")
        if len(self.candidate_comment_ids) > self.collected_total:
            raise ValueError("candidate_pool_exceeds_collected_total")
        if set(self.stratum_comment_ids) != self.available_strata:
            raise ValueError("stratum_keys_must_match_available_strata")
        candidates = set(self.candidate_comment_ids)
        for values in self.stratum_comment_ids.values():
            if len(values) != len(set(values)):
                raise ValueError("stratum_comment_ids_must_be_unique")
            if any(item not in candidates for item in values):
                raise ValueError("stratum_comment_id_not_in_candidate_pool")
        return self
