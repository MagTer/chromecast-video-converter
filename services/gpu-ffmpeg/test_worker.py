import sys
from pathlib import Path

import pytest

# Ensure we can import from app
sys.path.append(str(Path(__file__).parent))

from app.ffmpeg_builder import FFmpegBuilder


def test_prefers_non_commentary_when_multiple_streams_share_language():
    streams = [
        {
            "input_index": 0,
            "language": "eng",
            "disposition": {"default": 1, "comment": 1},
            "title": "Director Commentary",
        },
        {
            "input_index": 1,
            "language": "eng",
            "disposition": {"original": 1},
            "title": "Main Audio",
        },
    ]

    # Mock builder with minimal args
    builder = FFmpegBuilder({}, Path("."), Path("."), {}, {}, {})
    mapped, default_idx = builder._select_priority_streams(streams)

    assert [stream["input_index"] for stream in mapped] == [1]
    assert default_idx == 0


@pytest.mark.parametrize("languages", [("swe", "eng"), ("swe", None)])
def test_default_prefers_swedish_then_english(languages):
    swedish_language, english_language = languages
    streams = [
        {
            "input_index": 0,
            "language": english_language,
            "disposition": {"default": 1} if english_language else {},
            "title": "English Main" if english_language else "",
        },
        {
            "input_index": 1,
            "language": swedish_language,
            "disposition": {},
            "title": "Swedish Main",
        },
        {
            "input_index": 2,
            "language": "spa",
            "disposition": {"original": 1},
            "title": "Original Spanish",
        },
    ]

    builder = FFmpegBuilder({}, Path("."), Path("."), {}, {}, {})
    mapped, default_idx = builder._select_priority_streams(streams)

    assert mapped[0]["language"] == "swe"
    assert default_idx == 0
    # Ensure fallback/original is still kept for third priority when present.
    assert {stream["language"] for stream in mapped} >= {"swe"}
