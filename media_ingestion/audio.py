"""Decode locally staged media and produce verified 16 kHz mono PCM WAV."""

import wave

from .transport import MediaError

RATE = 16000


def decode_audio(source, destination, *, max_seconds, sample_seconds=None):
    import av

    samples = 0
    limit = int((sample_seconds or max_seconds) * RATE)
    try:
        options = {"protocol_whitelist": "file"}
        if source.suffix == ".m3u8":
            options.update(allowed_extensions="ALL", extension_picky="0")
        with av.open(str(source), options=options) as media:
            if not media.streams.audio:
                raise MediaError("no_audio_stream")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            with wave.open(str(destination), "wb") as output:
                output.setparams((1, 2, RATE, 0, "NONE", "not compressed"))

                def save(frames):
                    nonlocal samples
                    for frame in frames:
                        remaining = limit - samples
                        if frame.samples > remaining and sample_seconds is None:
                            raise MediaError("audio_duration_limit")
                        count = min(frame.samples, remaining)
                        if count:
                            output.writeframesraw(bytes(frame.planes[0])[:count * 2])
                            samples += count

                for frame in media.decode(audio=0):
                    save(resampler.resample(frame))
                    if sample_seconds is not None and samples >= limit:
                        break
                if samples < limit or sample_seconds is None:
                    save(resampler.resample(None))
        if not samples:
            raise MediaError("empty_decoded_audio")
        with av.open(str(destination), options={"protocol_whitelist": "file"}) as verified:
            stream = verified.streams.audio[0]
            if stream.rate != RATE or stream.codec_context.channels != 1:
                raise MediaError("unexpected_audio_format")
            decoded = sum(frame.samples for frame in verified.decode(audio=0))
        if samples != decoded:
            raise MediaError("audio_verification_mismatch")
        return {"sample_rate": RATE, "channels": 1, "samples": samples, "duration_seconds": samples / RATE}
    except (av.FFmpegError, IndexError, OSError, ValueError):
        raise MediaError("audio_decode_failed") from None
