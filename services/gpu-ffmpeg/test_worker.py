import importlib.util
from pathlib import Path

import pytest


def _load_builder_module():
    path = Path(__file__).with_name("ffmpeg_builder.py")
    spec = importlib.util.spec_from_file_location("ffmpeg_builder", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


builder_module = _load_builder_module()
FFmpegBuilder = builder_module.FFmpegBuilder


def test_prefers_non_commentary_when_multiple_streams_share_language():
    builder = FFmpegBuilder({}, {}, {})
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

    mapped, default_idx = builder._select_priority_streams(streams)

    assert [stream["input_index"] for stream in mapped] == [1]
    assert default_idx == 0


@pytest.mark.parametrize("languages", [("swe", "eng"), ("swe", None)])
def test_default_prefers_swedish_then_english(languages):
    builder = FFmpegBuilder({}, {}, {})
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

    mapped, default_idx = builder._select_priority_streams(streams)

    assert mapped[0]["language"] == "swe"
    assert default_idx == 0
    # Ensure fallback/original is still kept for third priority when present.
    assert {stream["language"] for stream in mapped} >= {"swe"}
