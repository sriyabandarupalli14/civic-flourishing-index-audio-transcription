import csv
import io
import json
import math
import struct
import unittest
import wave
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from media_ingestion.reporting import plan, report_outputs
from media_ingestion.audio import decode_audio
from media_ingestion.eligibility import assess
from media_ingestion.hls import load_playlist, parse_playlist, stage_hls
from media_ingestion.pipeline import Limits, MediaPipeline, Recording
from media_ingestion.refresh import refresh_public_reference
from media_ingestion.transport import MediaError, Resolved, expired, kind_for, resolve_player, transfer

AUDIO = "https://example.org/recording.wav"
SIGNED = "https://example.org/recording.wav?X-Amz-Date=20200101T000000Z&X-Amz-Expires=60"
ALTERNATE = "https://example.org/alternate.wav"
HLS = "https://example.org/master.m3u8"


class Response:
    def __init__(self, url, content=b"", status=200, mime="audio/wav", headers=None):
        self.url, self.content, self.status_code = url, content, status
        self.headers = {"Content-Type": mime, "Content-Length": str(len(content)), **(headers or {})}
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_content(self, size):
        self.reads += 1
        return (self.content[i:i + size] for i in range(0, len(self.content), size))


class Session:
    def __init__(self, get, head=405):
        self.get_reply = get
        self.head = lambda url, **kwargs: Response(url, status=head)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.get_reply(url)


def wave_bytes():
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as container:
        container.setparams((2, 2, 8000, 0, "NONE", "not compressed"))
        frames = bytearray()
        for sample in range(8000):
            value = int(5000 * math.sin(sample * 2 * math.pi * 200 / 8000))
            frames.extend(struct.pack("<hh", value, value))
        container.writeframes(frames)
    return buffer.getvalue()


class MediaTests(unittest.TestCase):
    def test_type_and_expiry_checks(self):
        self.assertEqual([kind_for(url) for url in (
            AUDIO, "https://example.org/v.MP4?token=x", "https://youtu.be/ABCDEFGHIJK",
            "https://soundcloud.com/example/example", HLS, "file:///tmp/a.mp3",
        )], ["audio", "video", "youtube", "soundcloud", "hls", "unsupported"])
        self.assertTrue(expired(SIGNED, datetime(2020, 1, 1, 0, 1, tzinfo=timezone.utc)))
        self.assertFalse(expired(SIGNED, datetime(2020, 1, 1, 0, 0, tzinfo=timezone.utc)))

    def test_http_rejects_wrong_type_and_over_budget_before_reading(self):
        for response, limit, code in (
            (Response(AUDIO, b"<html>", mime="text/html"), 16, "non_media_response"),
            (Response(AUDIO, b"RIFF" * 20), 16, "byte_limit_exceeded"),
            (Response(AUDIO, b"RIFF", status=206), 16, "unexpected_partial_response"),
        ):
            with self.assertRaisesRegex(MediaError, code):
                transfer(Session(lambda _: response), AUDIO, limit=limit)
            self.assertEqual(response.reads, 0)

    def test_probe_falls_back_when_head_is_not_supported(self):
        content = wave_bytes()
        session = Session(lambda url: Response(url, content))
        item = Recording("test", "church", "1", (AUDIO,))
        with TemporaryDirectory() as root:
            result = MediaPipeline(session, Path(root)).run(item, mode="probe")
        self.assertEqual(result["status"], "accessible_reference")
        self.assertEqual(result["head_status"], 405)
        self.assertEqual(result["prefix_bytes"], 4096)
        self.assertEqual(session.calls[0][1]["headers"]["Range"], "bytes=0-4095")

    def test_signed_expiration_refresh_and_alternative_on_transient_error(self):
        outcomes = iter([503, 503, 200])
        session = Session(lambda url: Response(url, wave_bytes(), status=next(outcomes)))
        item = Recording("test", "church", "1", (AUDIO, ALTERNATE, SIGNED))
        with TemporaryDirectory() as root:
            events = []
            result = MediaPipeline(session, Path(root), wait=lambda _: None).run(
                item, mode="probe", limits=Limits(attempts=2), log=events.append,
            )
            self.assertEqual(result["status"], "accessible_reference")
            self.assertEqual([url for url, _ in session.calls], [AUDIO, AUDIO, ALTERNATE])
            self.assertEqual([e["reason"] for e in events if e["status"] == "attempt_failed"], ["http_503"] * 2)
            fresh = Session(lambda url: Response(url, wave_bytes()))
            result = MediaPipeline(fresh, Path(root), refresh=lambda *_: ALTERNATE).run(
                Recording("expired", "church", "1", (SIGNED,)), mode="probe", limits=Limits(attempts=1),
            )
            self.assertTrue(result["refreshed"])
            self.assertEqual([url for url, _ in fresh.calls], [ALTERNATE])

    def test_full_and_sample_wav_checkpoint_and_candidate_split(self):
        item = Recording("test", "church", "1", (AUDIO,))
        session = Session(lambda url: Response(url, wave_bytes()))
        with TemporaryDirectory() as root:
            root = Path(root)
            pipeline = MediaPipeline(session, root / "audio")
            full = pipeline.run(item, mode="ingest")
            self.assertEqual((full["samples"], full["channels"], full["sample_rate"]), (16000, 1, 16000))
            self.assertEqual(pipeline.run(item, mode="ingest")["status"], "reused")
            self.assertEqual(len(session.calls), 1)
            sample = pipeline.run(item, mode="ingest", limits=Limits(sample_seconds=0.25))
            self.assertEqual(sample["samples"], 4000)
            self.assertEqual(report_outputs({item.identifier: item}, root),
                             {"full_audio_candidates": 1, "decoded_samples": 1})
            (root / "audio" / full["audio_file"]).write_bytes(b"corrupt")
            self.assertEqual(pipeline.run(item, mode="ingest")["status"], "decoded_audio")
            self.assertEqual(len(session.calls), 3)
            checkpoint = (root / "audio" / full["audio_file"]).with_suffix(".checkpoint.json")
            checkpoint.write_text("[]")
            self.assertEqual(pipeline.run(item, mode="ingest")["status"], "decoded_audio")

    def test_review_records_never_touch_network(self):
        item = Recording("held", "church", "1", (AUDIO,), review_flags=("source_identity_review",))
        session = Session(lambda _: self.fail("network requested for held recording"))
        with TemporaryDirectory() as root:
            root = Path(root)
            result = MediaPipeline(session, root / "audio").run(item, mode="ingest")
            self.assertEqual(result["status"], "held")
            self.assertEqual(plan({item.identifier: item}, root)["held_for_review"], 1)
            with (root / "media_review.csv").open() as handle:
                self.assertEqual(next(csv.DictReader(handle))["selection_status"], "church_identity_review")
            self.assertFalse((root / "audio").exists())

    def test_warning_only_recording_can_run_a_media_probe(self):
        flags = ("same_title_and_date_separate_groups", "non_media_asset_in_input")
        item = Recording("warning", "church", "34", (AUDIO, SIGNED), review_flags=flags,
                         source_record={"input_rows": "[10,12]", "audio_urls": json.dumps([AUDIO, SIGNED])})
        with TemporaryDirectory() as root:
            root = Path(root)
            self.assertEqual(plan({item.identifier: item}, root)["eligible_for_media_check"], 1)
            with (root / "media_plan.csv").open() as handle:
                row = next(csv.DictReader(handle))
            self.assertEqual(set(row["warning_flags"].split(";")), set(flags))
            self.assertEqual(row["review_flags"].split(";"), list(flags))
            self.assertEqual(row["input_rows"], "[10,12]")
            self.assertEqual(json.loads(row["audio_urls"]), [AUDIO, SIGNED])
            result = MediaPipeline(Session(lambda url: Response(url, wave_bytes())), root / "audio").run(
                item, mode="probe",
            )
            self.assertEqual(result["status"], "accessible_reference")

    def test_source_exceptions_are_limited_to_the_audited_patterns(self):
        for recording, expected in (
            (Recording("suffix", "church", "24", (AUDIO,), ("sermonnet", "monpres.sermon.net", "123"),
                       review_flags=("conflicting_title",), title_variants=("Sermon", "Sermon - Audio")), ""),
            (Recording("conflict", "church", "24", (AUDIO,), ("sermonnet", "monpres.sermon.net", "123"),
                       review_flags=("conflicting_title",), title_variants=("Sermon", "Different - Audio")),
             "recording_ambiguity_review"),
            (Recording("historical", "church", "39", (AUDIO,),
                       pages=("https://www.churchatbrierfield.org/sermon/example/",),
                       review_flags=("source_content_review",)), ""),
            (Recording("outside-route", "church", "39", (AUDIO,),
                       pages=("https://www.churchatbrierfield.org/other/",),
                       review_flags=("source_content_review",)), "content_relevance_review"),
        ):
            with self.subTest(recording=recording.identifier):
                self.assertEqual(recording.held_reason(), expected)

    def test_overlapping_holds_and_warnings_remain_visible(self):
        item = Recording("overlap", "church", "34", ("https://example.org/page",), review_flags=(
            "exact_url_multiple_platform_ids", "non_media_asset_in_input", "attachment_requires_sermon_review",
        ))
        disposition = assess(item)
        self.assertEqual(disposition.holds, ("no_supported_reference", "recording_ambiguity_review",
                                             "content_relevance_review"))
        self.assertEqual(disposition.warnings, ("non_media_asset_in_input",))
        self.assertEqual(Recording("unknown", "church", "34", (AUDIO,), review_flags=("new_flag",)).held_reason(),
                         "unclassified_review_required")

    def test_hls_master_audio_selection_segment_budget_and_query_preservation(self):
        audio = "https://example.org/audio.m3u8?sig=x"
        segment = "https://example.org/first.ts?sig=a%2Bb"
        second = "https://example.org/second.ts"
        pages = {
            HLS: b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="A",DEFAULT=YES,URI="audio.m3u8?sig=x"\n'
                 b'#EXT-X-STREAM-INF:BANDWIDTH=100,AUDIO="A"\nvideo.m3u8\n',
            audio: b"#EXTM3U\n#EXTINF:6,\nfirst.ts?sig=a%2Bb\n#EXTINF:6,\nsecond.ts\n#EXT-X-ENDLIST\n",
            segment: b"binary segment one",
            second: b"binary segment two",
        }
        session = Session(lambda url: Response(url, pages[url], mime="application/octet-stream"))
        source = Resolved(HLS, "hls", {})
        playlist = load_playlist(session, source)
        with TemporaryDirectory() as root:
            root = Path(root)
            staged, _ = stage_hls(session, source, playlist, root,
                                  max_bytes=200, max_seconds=100, sample_seconds=2)
            self.assertIn("segment-0.ts", staged.read_text())
            self.assertNotIn("segment-1", staged.read_text())
        self.assertEqual([u for u, _ in session.calls], [HLS, audio, segment])

    def test_hls_transport_and_decoder_with_real_local_aac_fixture(self):
        import av

        with TemporaryDirectory() as root:
            root = Path(root)
            segment = root / "fixture.ts"
            with av.open(str(segment), "w", format="mpegts") as encoder:
                stream = encoder.add_stream("aac", rate=16000)
                stream.codec_context.layout = "mono"
                for _ in range(16):
                    frame = av.AudioFrame(format="s16", layout="mono", samples=1024)
                    frame.sample_rate = 16000
                    frame.planes[0].update(bytes(frame.planes[0].buffer_size))
                    for packet in stream.encode(frame):
                        encoder.mux(packet)
                for packet in stream.encode(None):
                    encoder.mux(packet)
            data = segment.read_bytes()
            listing = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2,\nfirst.ts\n#EXT-X-ENDLIST\n"
            session = Session(lambda url: Response(
                url, listing if url == HLS else data, mime="application/octet-stream",
            ))
            result = MediaPipeline(session, root / "audio").run(
                Recording("hls", "church", "1", (HLS,)), mode="ingest",
                limits=Limits(max_bytes=1024 * 1024, sample_seconds=0.5),
            )
            self.assertEqual((result["status"], result["samples"]), ("decoded_audio", 8000))
            self.assertEqual(len(session.calls), 2)

    def test_hls_and_invalid_source_errors(self):
        with self.assertRaisesRegex(MediaError, "encrypted_hls"):
            parse_playlist(b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\n#EXTINF:1,\nfile.ts\n', HLS)
        with self.assertRaisesRegex(MediaError, "invalid_http_reference"):
            parse_playlist(b"#EXTM3U\n#EXTINF:1,\nfile:///tmp/a.ts\n", HLS)
        with TemporaryDirectory() as root:
            bad = Path(root) / "bad"
            bad.write_bytes(b"unrecognized data")
            with self.assertRaisesRegex(MediaError, "audio_decode_failed"):
                decode_audio(bad, Path(root) / "output.wav", max_seconds=10)

    def test_platform_resolution_passes_metadata_only_and_hls_headers(self):
        class Extractor:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *_):
                pass

            def extract_info(inner, url, *, download):
                self.assertFalse(download)
                self.assertTrue(inner.options["skip_download"])
                self.assertTrue(inner.options["noplaylist"])
                return {"url": HLS, "protocol": "m3u8_native", "vcodec": "none",
                        "http_headers": {"Referer": "https://example.org/", "Cookie": "test=unused"}}

        source = resolve_player("https://soundcloud.com/example/track", downloader=Extractor)
        self.assertEqual((source.kind, source.url, source.headers),
                         ("hls", HLS, {"Referer": "https://example.org/"}))

    def test_boxcast_refresh_uses_same_broadcast_id(self):
        old = "https://play.boxcast.com/p/obsolete/v/all.m3u8"
        new = "https://play.boxcast.com/p/current/v/all.m3u8?sign=fixture"
        item = Recording("test", "church", "12", (old,), ("boxcast", "broadcast", "individual"))
        session = Session(lambda url: Response(
            url, json.dumps({"status": "recorded", "playlist": new}).encode(), mime="application/json"))
        self.assertEqual(refresh_public_reference(session, item, old), new)
        self.assertIn("/broadcasts/individual/view?", session.calls[0][0])
        self.assertNotIn("obsolete", session.calls[0][0])
