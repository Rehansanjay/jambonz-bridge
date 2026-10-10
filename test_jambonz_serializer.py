"""The spec for the serializer, as tests, built from a real call.

Nothing here is invented. The preamble is the frame jambonz actually sent on
11 Oct 2026 (identifiers replaced), the frame size is the one size seen across
1514 frames, and the hold after killAudio is Sam Machin's answer in the jambonz
community Slack on 7 Oct. The numbers are in SERIALIZER.md.

These fail until `jambonz_serializer.py` exists. That is deliberate: this file
is the target, so the serializer can be written and checked without ngrok, a
softphone, or a phone call.

    .venv\\Scripts\\python.exe -m pytest test_jambonz_serializer.py -q
"""

from __future__ import annotations

import json
import time

import pytest

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup

from jambonz_serializer import JambonzFrameSerializer

# The real preamble, with the account's own identifiers swapped out. Shape,
# key names and types are exactly what arrived.
PREAMBLE = json.dumps(
    {
        "sampleRate": 16000,
        "mixType": "mono",
        "callSid": "3e5d869a-0000-0000-0000-000000000000",
        "direction": "inbound",
        "from": "rehan",
        "to": "1234",
        "callId": "94682bcc-0000-0000-0000-000000000000",
        "sipStatus": 200,
        "sipReason": "OK",
        "callStatus": "in-progress",
        "accountSid": "00000000-0000-0000-0000-000000000000",
        "traceId": "00000000000000000000000000000000",
    }
)

# 640 bytes was the only frame size across 1514 frames: 320 samples of 16-bit
# mono, which is exactly 20 ms at 16 kHz.
JAMBONZ_RATE = 16000
FRAME_BYTES = 640
PIPELINE_RATE = 24000  # deliberately different, so resampling has to happen


def a_frame(n: int = FRAME_BYTES) -> bytes:
    return b"\x01\x02" * (n // 2)


def a_setup() -> FrameProcessorSetup:
    """The smallest setup object the serializer needs.

    clock, task_manager and pipeline_worker have no defaults, so they have to be
    passed -- but setup() only reads the sample rate, so None is honest here
    rather than a stub pretending to be a clock.
    """
    return FrameProcessorSetup(
        clock=None,
        task_manager=None,
        pipeline_worker=None,
        audio_in_sample_rate=PIPELINE_RATE,
    )


async def connected(**kwargs) -> JambonzFrameSerializer:
    """A serializer that has been set up and has read the preamble."""
    serializer = JambonzFrameSerializer(**kwargs)
    await serializer.setup(a_setup())
    await serializer.deserialize(PREAMBLE)
    return serializer


# Pipecat's stream resampler does not emit one chunk per chunk. It buffers and
# flushes in bursts -- measured here as three empty returns and then everything
# at once on the fourth. So nothing in these tests may assume that one frame in
# produces one frame out; they pump a run of frames and look at the total, which
# is also what a real call does.
PUMP = 20


@pytest.mark.asyncio
async def test_the_rate_comes_from_the_wire_not_from_params():
    """The verb sets the fork rate per call and the socket states it in the
    preamble, so the serializer should believe the socket rather than require
    the caller to keep a constant in step with the verb array."""
    serializer = await connected()

    frames = [await serializer.deserialize(a_frame()) for _ in range(PUMP)]
    audio = [f for f in frames if f is not None]

    assert audio, "a run of audio frames produced nothing at all"
    assert all(isinstance(f, InputAudioRawFrame) for f in audio)
    assert all(f.sample_rate == PIPELINE_RATE for f in audio)  # resampled for the pipeline
    assert all(f.num_channels == 1 for f in audio)
    assert serializer.jambonz_sample_rate == JAMBONZ_RATE  # read, not assumed

    # 20 frames of 20 ms is 400 ms, which at the pipeline rate is this many
    # bytes. The resampler is still holding a tail, so allow for that rather
    # than pretending the arithmetic is exact.
    ideal = PUMP * FRAME_BYTES * PIPELINE_RATE // JAMBONZ_RATE
    got = sum(len(f.audio) for f in audio)
    assert 0.7 * ideal <= got <= 1.05 * ideal, f"{got} bytes out for an ideal of {ideal}"


@pytest.mark.asyncio
async def test_a_narrowband_call_is_followed_without_any_code_change():
    """8 kHz is valid for narrowband SIP. Nothing should need editing for it."""
    serializer = JambonzFrameSerializer()
    await serializer.setup(a_setup())
    await serializer.deserialize(json.dumps({**json.loads(PREAMBLE), "sampleRate": 8000}))

    assert serializer.jambonz_sample_rate == 8000


@pytest.mark.asyncio
async def test_audio_goes_out_as_raw_bytes_with_no_envelope():
    """Exotel base64s into JSON. jambonz takes raw L16, so serialize returns
    bytes -- and a str here would be sent as a TEXT frame and treated as
    control, not audio."""
    serializer = await connected()
    out_bytes = 960  # 20 ms at the pipeline rate

    sent = [
        await serializer.serialize(
            OutputAudioRawFrame(audio=a_frame(out_bytes), sample_rate=PIPELINE_RATE, num_channels=1)
        )
        for _ in range(PUMP)
    ]
    written = [s for s in sent if s is not None]

    assert written, "a run of bot audio produced nothing to write"
    for chunk in written:
        assert isinstance(chunk, bytes)
        assert not isinstance(chunk, str)  # a str would go out as TEXT, i.e. control

    # 400 ms at the pipeline rate, resampled down to the fork rate.
    ideal = PUMP * out_bytes * JAMBONZ_RATE // PIPELINE_RATE
    got = sum(len(c) for c in written)
    assert 0.7 * ideal <= got <= 1.05 * ideal, f"{got} bytes out for an ideal of {ideal}"


@pytest.mark.asyncio
async def test_an_interruption_becomes_killaudio():
    serializer = await connected()

    out = await serializer.serialize(InterruptionFrame())

    assert isinstance(out, str)  # control travels as TEXT
    assert json.loads(out) == {"type": "killAudio"}


@pytest.mark.asyncio
async def test_audio_is_held_briefly_after_killaudio():
    """The one that is not obvious from the docs.

    Sam Machin, jambonz community Slack, 7 Oct 2026: "the ordering of messages
    on the socket isn't 100% ... its best to have a small break after a
    killAudio before you send the next stream, 50-100ms should be plenty."

    So the serializer cannot simply stop. It has to stop *and wait*, which means
    holding the next audio frames itself -- Pipecat will keep calling serialize
    well inside that window.
    """
    serializer = await connected()

    def bot_audio() -> OutputAudioRawFrame:
        return OutputAudioRawFrame(
            audio=a_frame(960), sample_rate=PIPELINE_RATE, num_channels=1
        )

    await serializer.serialize(InterruptionFrame())

    # Every frame inside the window, not just the first: Pipecat keeps handing
    # over the interrupted turn's audio and all of it has to be withheld.
    inside = [await serializer.serialize(bot_audio()) for _ in range(5)]
    assert all(
        s is None for s in inside
    ), "audio sent inside the guard window can play over the caller"

    time.sleep(0.12)

    after = [await serializer.serialize(bot_audio()) for _ in range(PUMP)]
    written = [s for s in after if s is not None]
    assert written, "the guard must open again, not latch shut"
    assert all(isinstance(s, bytes) for s in written)


@pytest.mark.asyncio
async def test_audio_before_the_preamble_is_not_mistaken_for_a_call():
    """The preamble arrives first on a real socket, but a serializer that
    assumes it has cannot be reasoned about if it ever does not."""
    serializer = JambonzFrameSerializer()
    await serializer.setup(a_setup())

    out = await serializer.deserialize(a_frame())

    assert out is None or isinstance(out, InputAudioRawFrame)


@pytest.mark.asyncio
async def test_the_call_identifiers_are_kept_for_correlation():
    """callSid and traceId are what tie a Pipecat session to a jambonz call in
    logs. They arrive free in the preamble; throwing them away means threading
    them through the verb's url instead."""
    serializer = await connected()

    assert serializer.call_sid == "3e5d869a-0000-0000-0000-000000000000"
    assert serializer.trace_id == "00000000000000000000000000000000"
