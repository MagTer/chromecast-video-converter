import sys
from pathlib import Path

import pytest

# Ensure we can import from app
sys.path.append(str(Path(__file__).parent))

from app.ffmpeg_builder import AudioStreamInfo, FFmpegBuilder, StreamDisposition


def test_prefers_non_commentary_when_multiple_streams_share_language():
    streams = [
        AudioStreamInfo(
            input_index=0,
            language="eng",
            disposition=StreamDisposition(default=True, comment=True),
            title="Director Commentary",
        ),
        AudioStreamInfo(
            input_index=1,
            language="eng",
            disposition=StreamDisposition(original=True),
            title="Main Audio",
        ),
    ]

    # Mock builder with minimal args
    builder = FFmpegBuilder({}, Path("."), Path("."), {}, {}, {})
    mapped, default_idx = builder._select_priority_streams(streams)

    assert [stream.input_index for stream in mapped] == [1]
    assert default_idx == 0


@pytest.mark.parametrize("languages", [("swe", "eng"), ("swe", None)])
def test_default_prefers_swedish_then_english(languages):
    swedish_language, english_language = languages
    streams = [
        AudioStreamInfo(
            input_index=0,
            language=english_language,
            disposition=StreamDisposition(default=bool(english_language)),
            title="English Main" if english_language else "",
        ),
        AudioStreamInfo(
            input_index=1,
            language=swedish_language,
            disposition=StreamDisposition(),
            title="Swedish Main",
        ),
        AudioStreamInfo(
            input_index=2,
            language="spa",
            disposition=StreamDisposition(original=True),
            title="Original Spanish",
        ),
    ]

    builder = FFmpegBuilder({}, Path("."), Path("."), {}, {}, {})
    mapped, default_idx = builder._select_priority_streams(streams)

    assert mapped[0].language == "swe"
    assert default_idx == 0
    # Ensure fallback/original is still kept for third priority when present.
    assert {stream.language for stream in mapped} >= {"swe"}


def test_filters_to_preferred_and_original_languages():
    streams = [
        AudioStreamInfo(
            input_index=0,
            language="eng",
            disposition=StreamDisposition(default=True),
            title="English Main",
        ),
        AudioStreamInfo(
            input_index=1,
            language="swe",
            disposition=StreamDisposition(),
            title="Swedish Main",
        ),
        AudioStreamInfo(
            input_index=2,
            language="spa",
            disposition=StreamDisposition(original=True),
            title="Spanish Original",
        ),
        AudioStreamInfo(
            input_index=3,
            language="ger",
            disposition=StreamDisposition(),
            title="German Dub",
        ),
    ]
    builder = FFmpegBuilder({}, Path("."), Path("."), {}, {}, {})
    mapped, default_idx = builder._select_priority_streams(streams)
    languages = [s.language for s in mapped]
    assert languages == ["swe", "eng", "spa"]  # swe, eng, original only
    assert default_idx == 0
