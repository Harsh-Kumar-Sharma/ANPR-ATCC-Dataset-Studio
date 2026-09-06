from app.services.track_metrics import TrackSummary, find_duplicate_track_pairs, find_fragmented_track_pairs


def test_overlapping_tracks_in_the_same_place_are_flagged_duplicate():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(15, 10, 65, 60))
    b = TrackSummary("b", start_ts=200, end_ts=1200, first_bbox=(12, 10, 62, 60), last_bbox=(16, 10, 66, 60))

    pairs = find_duplicate_track_pairs([a, b])
    assert pairs == [("a", "b")]


def test_overlapping_tracks_in_different_places_are_not_duplicate():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(15, 10, 65, 60))
    b = TrackSummary("b", start_ts=200, end_ts=1200, first_bbox=(500, 500, 550, 550), last_bbox=(500, 500, 550, 550))

    assert find_duplicate_track_pairs([a, b]) == []


def test_non_overlapping_tracks_are_never_duplicate_regardless_of_position():
    a = TrackSummary("a", start_ts=0, end_ts=500, first_bbox=(10, 10, 60, 60), last_bbox=(10, 10, 60, 60))
    b = TrackSummary("b", start_ts=600, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(10, 10, 60, 60))

    assert find_duplicate_track_pairs([a, b]) == []


def test_a_track_ending_just_before_another_starts_nearby_is_fragmented():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(50, 10, 100, 60))
    b = TrackSummary("b", start_ts=1200, end_ts=2000, first_bbox=(52, 10, 102, 60), last_bbox=(90, 10, 140, 60))

    pairs = find_fragmented_track_pairs([a, b], gap_threshold_ms=500, iou_threshold=0.3)
    assert pairs == [("a", "b")]


def test_a_large_gap_is_not_fragmentation():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(50, 10, 100, 60))
    b = TrackSummary("b", start_ts=10_000, end_ts=11_000, first_bbox=(52, 10, 102, 60), last_bbox=(90, 10, 140, 60))

    assert find_fragmented_track_pairs([a, b], gap_threshold_ms=500) == []


def test_overlapping_tracks_are_not_double_counted_as_fragmentation():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(10, 10, 60, 60))
    b = TrackSummary("b", start_ts=500, end_ts=1500, first_bbox=(10, 10, 60, 60), last_bbox=(10, 10, 60, 60))

    # These overlap in time - that's duplicate-candidate territory, not fragmentation.
    assert find_fragmented_track_pairs([a, b]) == []


def test_nearby_but_spatially_distant_gap_is_not_fragmentation():
    a = TrackSummary("a", start_ts=0, end_ts=1000, first_bbox=(10, 10, 60, 60), last_bbox=(10, 10, 60, 60))
    b = TrackSummary("b", start_ts=1100, end_ts=2000, first_bbox=(900, 900, 950, 950), last_bbox=(900, 900, 950, 950))

    assert find_fragmented_track_pairs([a, b], gap_threshold_ms=500) == []


def test_empty_track_list_produces_no_pairs():
    assert find_duplicate_track_pairs([]) == []
    assert find_fragmented_track_pairs([]) == []
