import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[1]))

from app.compliance import compliance_failure, evaluate_chromecast_compliance


def make_analysis(
    video_overrides=None, audio_overrides=None, format_name="mov,mp4,m4a,3gp,3g2,mj2"
):
    video = {
        "codec_type": "video",
        "codec_name": "h264",
        "profile": "High",
        "level": 41,
        "width": 1920,
        "height": 1080,
        "pix_fmt": "yuv420p",
        "avg_frame_rate": "30/1",
    }
    video.update(video_overrides or {})
    audio = {"codec_type": "audio", "codec_name": "aac", "channels": 2}
    audio.update(audio_overrides or {})
    return {"format": {"format_name": format_name}, "streams": [video, audio]}


def test_compliant_1080p30_high_l41():
    result = evaluate_chromecast_compliance(make_analysis())
    assert result["compliant"] is True
    assert result["issues"] == []
    assert result["video"]["width"] == 1920


def test_width_beyond_decoder_limit_flagged():
    result = evaluate_chromecast_compliance(make_analysis({"width": 2592, "level": 51}))
    assert result["compliant"] is False
    assert any("Width 2592" in issue for issue in result["issues"])
    assert any("level 5.1 exceeds" in issue for issue in result["issues"])


def test_hdr_10bit_flagged():
    result = evaluate_chromecast_compliance(
        make_analysis({"pix_fmt": "yuv420p10le", "color_transfer": "smpte2084"})
    )
    assert result["compliant"] is False
    issues = " ".join(result["issues"])
    assert "10-bit" in issues
    assert "HDR" in issues


def test_wrong_video_codec_flagged():
    result = evaluate_chromecast_compliance(make_analysis({"codec_name": "hevc"}))
    assert result["compliant"] is False
    assert any("not H.264" in issue for issue in result["issues"])


def test_level_too_low_for_1080p60():
    result = evaluate_chromecast_compliance(make_analysis({"avg_frame_rate": "60/1"}))
    assert result["compliant"] is False
    assert any("too low" in issue for issue in result["issues"])


def test_level_42_allows_1080p60():
    result = evaluate_chromecast_compliance(make_analysis({"avg_frame_rate": "60/1", "level": 42}))
    assert result["compliant"] is True


def test_surround_audio_flagged():
    result = evaluate_chromecast_compliance(
        make_analysis(audio_overrides={"codec_name": "eac3", "channels": 6})
    )
    assert result["compliant"] is False
    issues = " ".join(result["issues"])
    assert "not AAC" in issues
    assert "6 channels" in issues


def test_non_mp4_container_flagged():
    result = evaluate_chromecast_compliance(make_analysis(format_name="matroska,webm"))
    assert result["compliant"] is False
    assert any("Container" in issue for issue in result["issues"])


def test_attached_pictures_are_ignored():
    analysis = make_analysis()
    analysis["streams"].insert(
        0,
        {
            "codec_type": "video",
            "codec_name": "mjpeg",
            "disposition": {"attached_pic": 1},
            "width": 600,
            "height": 900,
        },
    )
    result = evaluate_chromecast_compliance(analysis)
    assert result["compliant"] is True


def test_empty_analysis_is_non_compliant():
    result = evaluate_chromecast_compliance({})
    assert result["compliant"] is False
    assert result["issues"]


def test_compliance_failure_helper():
    result = compliance_failure(["Output file not found"])
    assert result["compliant"] is False
    assert result["issues"] == ["Output file not found"]
    assert result["checked_at"]
