import json
from datetime import datetime, timezone

from studio.publish import buffer_services_for
from studio.ytsync import effective_schedule, is_due, upload_pending

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
LONG = {"format": "long"}
SHORT = {"format": "short"}
ALL = ["youtube", "facebook", "linkedin", "instagram"]


def test_is_due_within_48_hours():
    assert is_due(None, NOW)
    assert is_due("2026-10-05T15:00:00.000Z", NOW)       # 27 h away
    assert not is_due("2026-10-07T15:00:00.000Z", NOW)   # 75 h away


def test_missed_time_becomes_public_now():
    assert effective_schedule("2026-10-04T11:00:00.000Z", False, NOW) == (None, True)
    assert effective_schedule("2026-10-05T15:00:00.000Z", False, NOW) == (
        "2026-10-05T15:00:00.000Z", False)
    assert effective_schedule(None, False, NOW) == (None, False)


def test_youtube_never_goes_through_buffer_and_instagram_only_gets_shorts():
    assert buffer_services_for(SHORT, ALL) == ["facebook", "linkedin", "instagram"]
    assert buffer_services_for(LONG, ALL) == ["facebook", "linkedin"]


def test_upload_pending_skips_when_not_due_or_already_uploaded(tmp_path):
    class Cfg:  # upload_pending must return before touching anything else
        youtube = None

    state = {"posts": {}, "youtube_pending": {"publish_at": "2026-10-09T15:00:00.000Z",
                                              "public_now": False}}
    (tmp_path / "publish.json").write_text(json.dumps(state), encoding="utf-8")
    assert upload_pending(Cfg, tmp_path, NOW) is None

    done = {**state, "youtube_direct": {"video_id": "x"}}
    (tmp_path / "publish.json").write_text(json.dumps(done), encoding="utf-8")
    assert upload_pending(Cfg, tmp_path, NOW) is None
