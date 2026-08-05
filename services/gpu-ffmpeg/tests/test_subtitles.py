# Add services/gpu-ffmpeg to path so we can import app
import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parents[1]))

from app import worker  # type: ignore  # noqa: E402


def _analysis_with_subs() -> dict:
    return {
        "streams": [
            {"codec_type": "video", "codec_name": "hevc"},
            {"codec_type": "audio", "codec_name": "eac3"},
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng"},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "hdmv_pgs_subtitle",
                "tags": {"language": "eng"},
            },
            {
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "ger"},
            },
            {"codec_type": "subtitle", "codec_name": "ass"},  # untagged language
        ]
    }


class FakeProcess:
    def __init__(self, returncode: int, sidecar: Path | None):
        self.returncode = returncode
        self._sidecar = sidecar

    async def communicate(self):
        if self.returncode == 0 and self._sidecar is not None:
            self._sidecar.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n")
        return b"ffmpeg output", None


def _patch_ffmpeg(monkeypatch, commands: list[list[str]], returncode: int = 0):
    async def fake_create(*args, **kwargs):  # noqa: ARG001
        command = list(args)
        commands.append(command)
        sidecar = Path(command[-1]) if returncode == 0 else None
        return FakeProcess(returncode, sidecar)

    monkeypatch.setattr(worker.asyncio, "create_subprocess_exec", fake_create)


def test_extract_text_subtitles_selects_streams(monkeypatch, tmp_path):
    output = tmp_path / "movie-chromecast.mp4"
    commands: list[list[str]] = []
    _patch_ffmpeg(monkeypatch, commands)

    extracted = asyncio.run(
        worker._extract_text_subtitles(tmp_path / "movie.mkv", output, _analysis_with_subs())
    )

    # eng subrip (0:s:0) and the untagged ass stream (0:s:3) are exported;
    # the PGS bitmap stream and the non-preferred German track are skipped.
    maps = [cmd[cmd.index("-map") + 1] for cmd in commands]
    assert maps == ["0:s:0", "0:s:3"]
    assert [p.name for p in extracted] == [
        "movie-chromecast.eng.sub0.srt",
        "movie-chromecast.und.sub3.srt",
    ]
    assert all(p.exists() for p in extracted)


def test_extract_text_subtitles_skips_existing_sidecar(monkeypatch, tmp_path):
    output = tmp_path / "movie-chromecast.mp4"
    existing = tmp_path / "movie-chromecast.eng.sub0.srt"
    existing.write_text("already there")
    commands: list[list[str]] = []
    _patch_ffmpeg(monkeypatch, commands)

    analysis = {
        "streams": [{"codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}}]
    }
    extracted = asyncio.run(
        worker._extract_text_subtitles(tmp_path / "movie.mkv", output, analysis)
    )
    assert extracted == [existing]
    assert commands == []  # no ffmpeg run needed


def test_extract_text_subtitles_tolerates_failure(monkeypatch, tmp_path):
    output = tmp_path / "movie-chromecast.mp4"
    commands: list[list[str]] = []
    _patch_ffmpeg(monkeypatch, commands, returncode=1)

    extracted = asyncio.run(
        worker._extract_text_subtitles(tmp_path / "movie.mkv", output, _analysis_with_subs())
    )
    assert extracted == []
    assert len(commands) == 2  # both candidates attempted, neither raised


def test_subtitle_report_flags_lost_subtitles(monkeypatch, tmp_path):
    source = tmp_path / "movie.mkv"
    output = tmp_path / "movie-chromecast.mp4"
    output.write_bytes(b"converted")

    def fake_probe(path):
        if Path(path) == output:
            return {"streams": [{"codec_type": "video", "codec_name": "h264"}]}
        return _analysis_with_subs()

    monkeypatch.setattr(worker, "probe_file", fake_probe)

    report = asyncio.run(worker._subtitle_report(source, output))
    assert report == {"source_streams": 4, "embedded": 0, "sidecars": 0, "preserved": False}

    # A sidecar next to the output flips the verdict
    (tmp_path / "movie-chromecast.eng.sub0.srt").write_text("content")
    report = asyncio.run(worker._subtitle_report(source, output))
    assert report["sidecars"] == 1
    assert report["preserved"] is True


def test_subtitle_report_preserved_without_source_subs(monkeypatch, tmp_path):
    source = tmp_path / "movie.mkv"
    output = tmp_path / "movie-chromecast.mp4"

    monkeypatch.setattr(worker, "probe_file", lambda path: {"streams": []})
    report = asyncio.run(worker._subtitle_report(source, output))
    assert report["preserved"] is True
    assert report["source_streams"] == 0
