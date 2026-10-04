import json

import httpx
import pytest

from studio.buffer import BufferClient, BufferError
from studio.hosting import parse_cloudinary_url, sign
from studio.publish import (
    PublishError,
    build_post_input,
    hashtags,
    parse_when,
    social_text,
    youtube_text,
    youtube_title,
)
from studio.script import validate_script

SHORT = validate_script({"title": "Fill Empty Salon Slots", "description": "Para one.\nPara two.",
                         "tags": ["salon marketing", "Salon Marketing", "small business"],
                         "scenes": [{"narration": "Hi."}]}, "short")
LONG = {**SHORT, "format": "long"}
TEXTS = {"youtube": "YT text", "social": "Social text"}


def test_parse_when_modes():
    assert parse_when("queue") == ("addToQueue", None)
    assert parse_when("now") == ("shareNow", None)
    assert parse_when("2026-10-06T15:00+00:00") == ("customScheduled", "2026-10-06T15:00:00.000Z")
    with pytest.raises(PublishError):
        parse_when("next tuesday")


def test_hashtags_dedupe_and_limit():
    assert hashtags(["salon marketing", "Salon Marketing", "small business", "x"], 2) == \
        "#SalonMarketing #SmallBusiness"


def test_youtube_title_adds_shorts_tag_only_for_shorts():
    assert youtube_title(SHORT) == "Fill Empty Salon Slots #Shorts"
    assert youtube_title(LONG) == "Fill Empty Salon Slots"


def test_texts_include_cta_chapters_and_tags():
    assert youtube_text(LONG, "Free audit", ["0:00 Hook"]).endswith("Chapters:\n0:00 Hook")
    social = social_text(SHORT, "Follow us", "#Salon")
    assert social == "Fill Empty Salon Slots\n\nPara one.\n\nFollow us\n\n#Salon"


def test_youtube_post_input():
    post = build_post_input("youtube", "ch1", SHORT, TEXTS, "https://v/1.mp4", "addToQueue",
                            None, True, {"youtube_category": "27"})

    assert post["channelId"] == "ch1"
    assert post["text"] == "YT text"
    assert post["saveToDraft"] is True
    assert post["needsApproval"] is False
    assert post["assets"] == [{"video": {"url": "https://v/1.mp4"}}]
    assert post["metadata"]["youtube"]["title"].endswith("#Shorts")
    assert post["metadata"]["youtube"]["categoryId"] == "27"
    assert "dueAt" not in post


def test_facebook_reel_for_shorts_post_for_long_and_due_date():
    short = build_post_input("facebook", "f", SHORT, TEXTS, "u", "customScheduled",
                             "2026-10-06T15:00:00.000Z", False, {})
    long = build_post_input("facebook", "f", LONG, TEXTS, "u", "addToQueue", None, False, {})

    assert short["metadata"] == {"facebook": {"type": "reel"}}
    assert short["dueAt"] == "2026-10-06T15:00:00.000Z"
    assert short["text"] == "Social text"
    assert long["metadata"] == {"facebook": {"type": "post"}}


def test_linkedin_has_no_metadata_and_unknown_service_fails():
    assert "metadata" not in build_post_input("linkedin", "l", SHORT, TEXTS, "u", "addToQueue",
                                              None, True, {})
    with pytest.raises(PublishError):
        build_post_input("myspace", "m", SHORT, TEXTS, "u", "addToQueue", None, True, {})


def test_parse_cloudinary_url():
    creds = parse_cloudinary_url("cloudinary://123:abc@my-cloud")
    assert (creds.cloud_name, creds.api_key, creds.api_secret) == ("my-cloud", "123", "abc")
    assert parse_cloudinary_url("https://example.com") is None
    assert parse_cloudinary_url(None) is None


def test_sign_matches_cloudinary_documented_example():
    params = {"eager": "w_400,h_300,c_pad|w_260,h_200,c_crop", "public_id": "sample_image",
              "timestamp": "1315060510"}
    assert sign(params, "abcd") == "bfd09f95f331f558cbd1320e67aa8d488770583e"


def _client(handler) -> BufferClient:
    return BufferClient("key", transport=httpx.MockTransport(handler))


def test_buffer_create_post_returns_post_and_sends_bearer_key():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer key"
        assert json.loads(request.content)["variables"]["input"]["channelId"] == "c"
        return httpx.Response(200, json={"data": {"createPost": {"post": {"id": "p1"}}}})

    assert _client(handler).create_post({"channelId": "c"}) == {"id": "p1"}


def test_buffer_mutation_error_and_graphql_errors_raise():
    refused = _client(lambda r: httpx.Response(
        200, json={"data": {"createPost": {"message": "Video too long"}}}))
    with pytest.raises(BufferError, match="Video too long"):
        refused.create_post({})

    broken = _client(lambda r: httpx.Response(200, json={"errors": [{"message": "Bad field"}]}))
    with pytest.raises(BufferError, match="Bad field"):
        broken.organizations()


def test_buffer_retries_when_rate_limited_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(200, json={"errors": [{"message": "Too many requests from this client."}]})
        return httpx.Response(200, json={"data": {"account": {"organizations": [{"id": "o"}]}}})

    waits = []
    client = BufferClient("k", transport=httpx.MockTransport(handler), sleep=waits.append)

    assert client.organizations() == [{"id": "o"}]
    assert waits == [10, 30]


def test_buffer_gives_up_after_retries():
    client = BufferClient("k", transport=httpx.MockTransport(lambda r: httpx.Response(429)),
                          sleep=lambda s: None)
    with pytest.raises(BufferError, match="limiting requests"):
        client.organizations()


def test_buffer_bad_key_and_missing_key():
    with pytest.raises(BufferError, match="rejected the API key"):
        _client(lambda r: httpx.Response(401, json={})).organizations()
    with pytest.raises(BufferError, match="No Buffer API key"):
        BufferClient("")
