import json

from studio.publish import youtube_privacy
from studio.youtube import build_metadata, load_credentials, trim_tags


def test_scheduled_upload_is_private_with_publish_time():
    meta = build_metadata("T", "D", ["a"], "27", "2026-10-05T14:00:00.000Z", False, False)

    assert meta["status"]["privacyStatus"] == "private"
    assert meta["status"]["publishAt"] == "2026-10-05T14:00:00.000Z"
    assert meta["status"]["selfDeclaredMadeForKids"] is False
    assert meta["snippet"]["categoryId"] == "27"


def test_unscheduled_upload_public_or_private():
    assert build_metadata("T", "D", [], "27", None, True, False)["status"] == {
        "selfDeclaredMadeForKids": False, "containsSyntheticMedia": False,
        "privacyStatus": "public"}
    assert build_metadata("T", "D", [], "27", None, False, True)["status"]["privacyStatus"] == "private"


def test_metadata_trims_long_title():
    assert len(build_metadata("x" * 150, "D", [], "27", None, True, False)["snippet"]["title"]) == 100


def test_trim_tags_respects_limit():
    tags = ["small business"] * 100
    kept = trim_tags(tags, limit=50)
    assert 0 < len(kept) < 100
    assert sum(len(t) + 3 for t in kept) <= 50


def test_youtube_privacy_modes():
    assert youtube_privacy("customScheduled", "2026-10-05T14:00:00.000Z", False) == (
        "2026-10-05T14:00:00.000Z", False)
    assert youtube_privacy("shareNow", None, False) == (None, True)
    assert youtube_privacy("customScheduled", "2026-10-05T14:00:00.000Z", True) == (None, False)


def test_load_credentials_from_env_and_files(tmp_path):
    env = {"YOUTUBE_CLIENT_ID": "id", "YOUTUBE_CLIENT_SECRET": "s", "YOUTUBE_REFRESH_TOKEN": "r"}
    assert load_credentials(tmp_path, env).refresh_token == "r"
    assert load_credentials(tmp_path, {"YOUTUBE_REFRESH_TOKEN": "r"}) is None
    assert load_credentials(tmp_path, {}) is None

    (tmp_path / "client_secret.json").write_text(
        json.dumps({"installed": {"client_id": "cid", "client_secret": "cs"}}), encoding="utf-8")
    (tmp_path / "youtube_token.json").write_text(json.dumps({"refresh_token": "rt"}), encoding="utf-8")
    creds = load_credentials(tmp_path, {})
    assert (creds.client_id, creds.client_secret, creds.refresh_token) == ("cid", "cs", "rt")
