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


@pytest.mark.asyncio
async def test_the_rate_comes_from_the_wire_not_from_params():
    """The verb sets the fork rate per call and the socket states it in the
    preamble, so the serializer should believe the socket rather than require
    the caller to keep a constant in step with the verb array."""
    serializer = await connected()

    out = await serializer.deserialize(a_frame())

    assert isinstance(out, InputAudioRawFrame)
    assert out.sample_rate == PIPELINE_RATE  # resampled for the pipeline
    assert out.num_channels == 1
    assert serializer.jambonz_sample_rate == JAMBONZ_RATE  # read, not assumed


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

    out = await serializer.serialize(
        OutputAudioRawFrame(audio=a_frame(960), sample_rate=PIPELINE_RATE, num_channels=1)
    )

    assert isinstance(out, bytes)
    assert not isinstance(out, str)
    # 960 bytes at 24 kHz is 20 ms; the same 20 ms at 16 kHz is 640 bytes.
    assert out == b"" or abs(len(out) - FRAME_BYTES) <= 64


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
    await serializer.serialize(InterruptionFrame())

    immediately = await serializer.serialize(
        OutputAudioRawFrame(audio=a_frame(960), sample_rate=PIPELINE_RATE, num_channels=1)
    )
    assert immediately is None, "audio sent inside the guard window can play over the caller"

    time.sleep(0.12)
    after = await serializer.serialize(
        OutputAudioRawFrame(audio=a_frame(960), sample_rate=PIPELINE_RATE, num_channels=1)
    )
    assert isinstance(after, bytes) and after, "the guard must open again, not latch shut"


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
