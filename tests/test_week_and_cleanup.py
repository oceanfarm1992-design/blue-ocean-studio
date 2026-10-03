from datetime import date, datetime, time

import pytest

from studio.cleanup import cleanup_decision
from studio.hosting import public_id_from_url
from studio.publish import PublishError
from studio.week import next_monday, pick_ideas, schedule_times


def test_next_monday_is_always_in_the_future():
    assert next_monday(date(2026, 10, 3)) == date(2026, 10, 5)   # Saturday
    assert next_monday(date(2026, 10, 5)) == date(2026, 10, 12)  # Monday -> next week
    assert next_monday(date(2026, 10, 11)) == date(2026, 10, 12) # Sunday


def test_pick_ideas_prefers_long_then_shorts():
    ideas = [{"title": "a", "format": "short"}, {"title": "b", "format": "long"},
             {"title": "c", "format": "long"}, {"title": "d", "format": "short"}]
    long_idea, shorts = pick_ideas(ideas)

    assert long_idea["title"] == "b"
    assert [i["title"] for i in shorts] == ["a", "d"]


def test_pick_ideas_falls_back_when_no_long_or_few_shorts():
    long_idea, shorts = pick_ideas([{"title": "a", "format": "short"},
                                    {"title": "b", "format": "short"}])
    assert long_idea["title"] == "a"
    assert [i["title"] for i in shorts] == ["b"]
    with pytest.raises(PublishError):
        pick_ideas([])


def test_schedule_times_one_short_per_day():
    long_time, shorts = schedule_times(date(2026, 10, 5), time(17), time(18), 3)

    assert long_time == datetime(2026, 10, 5, 17)
    assert shorts == [datetime(2026, 10, 5, 18), datetime(2026, 10, 6, 18),
                      datetime(2026, 10, 7, 18)]


@pytest.mark.parametrize("statuses, expected", [
    (["sent", "sent"], "delete"),
    (["sent", None], "delete"),          # one post was deleted in Buffer
    ([None], "delete"),
    (["sent", "scheduled"], "keep"),
    (["draft"], "keep"),
    (["sent", "error"], "error"),
    ([], "keep"),
])
def test_cleanup_decision(statuses, expected):
    assert cleanup_decision(statuses) == expected


def test_public_id_from_url():
    url = "https://res.cloudinary.com/demo/video/upload/v1791017787/blue-ocean-marketing/my-video.mp4"
    assert public_id_from_url(url) == "blue-ocean-marketing/my-video"
    assert public_id_from_url("https://example.com/file.mp4") is None
