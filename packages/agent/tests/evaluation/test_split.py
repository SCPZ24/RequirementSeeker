from hashlib import sha256

from requirementseeker_agent.evaluation import stable_video_split


def records() -> list[str]:
    return ["video-3", "video-1", "video-2", "video-1", "video-5", "video-4"]


def test_video_split_never_leaks_comments() -> None:
    split = stable_video_split(records())

    assert set(split.development).isdisjoint(split.calibration)
    assert set(split.development).isdisjoint(split.holdout)
    assert set(split.calibration).isdisjoint(split.holdout)
    assert set(split.development + split.calibration + split.holdout) == set(records())


def test_video_split_is_deduplicated_and_hash_ordered() -> None:
    split = stable_video_split(reversed(records()))
    expected = sorted(set(records()), key=lambda item: sha256(item.encode()).digest())

    assert split.development == expected[:2]
    assert split.calibration == expected[2:4]
    assert split.holdout == expected[4:]


def test_ten_unique_videos_split_four_three_three() -> None:
    split = stable_video_split(f"video-{index}" for index in range(10))

    assert len(split.development) == 4
    assert len(split.calibration) == 3
    assert len(split.holdout) == 3
