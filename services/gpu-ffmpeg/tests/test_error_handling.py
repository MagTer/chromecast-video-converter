# Add services/gpu-ffmpeg to path so we can import app
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[1]))

from app.worker import (  # type: ignore  # noqa: E402
    _augment_analysis_with_video_metadata,
    classify_ffmpeg_error,
)


def test_classify_ffmpeg_error_retryable_and_category():
    logs = [
        "Some info",
        "Device busy or resource temporarily unavailable",
    ]
    result = classify_ffmpeg_error(logs, return_code=1)
    assert result["category"] == "device_busy"
    assert result["retryable"] is True


def test_classify_ffmpeg_error_unknown_defaults():
    result = classify_ffmpeg_error([], return_code=1)
    assert result["category"] == "unknown"
    assert result["retryable"] is False


def test_augment_analysis_adds_hdr_metadata():
    analysis = {
        "streams": [
            {
                "codec_type": "video",
                "pix_fmt": "yuv420p10le",
                "color_transfer": "smpte2084",
            }
        ]
    }
    enriched = _augment_analysis_with_video_metadata(analysis)
    assert enriched["streams"][0]["is_hdr"] is True
    assert enriched["streams"][0]["bit_depth"] == 10
