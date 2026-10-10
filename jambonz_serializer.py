"""A Pipecat FrameSerializer for jambonz's `listen` verb.

Pipecat ships serializers for Twilio, Telnyx, Plivo, Exotel, Vonage and Genesys.
There is none for jambonz, so a Pipecat developer choosing a phone layer has six
options and jambonz is not one of them. This is that file.

Everything here that is a number or a shape came off a real call on 11 Oct 2026,
measured with `probe_jambonz_socket.py`. The working is in SERIALIZER.md. Three
things make it differ from the Exotel serializer it is closest to:

1. **Audio has no envelope.** Exotel base64s PCM into a JSON `media` event.
   jambonz sends raw 16-bit signed little-endian mono on the socket, so
   `serialize` returns `bytes` for audio and `deserialize` goes straight to the
   resampler with no parsing step.

2. **The rate is not a constant.** Exotel hardcodes 8000. jambonz sets the fork
   rate per call through the verb's `sampleRate` and then *states it* in a JSON
   preamble before any audio. So the rate is read from the wire; a params
   override exists for odd cases but the default is to believe the socket.

3. **An interruption cannot just stop.** `killAudio` flushes what is playing,
   but ordering on the socket is not guaranteed -- small control messages can be
   prioritised ahead of larger audio frames, so a chunk written in the same
   instant can land *after* the flush and play over the caller. The fix, from
   Sam Machin in the jambonz community Slack on 7 Oct 2026, is to leave a
   50-100ms gap. Pipecat's interruption model has no place to put that, so the
   serializer holds the frames itself.

Checked by `test_jambonz_serializer.py`, which needs no phone call.
"""

from __future__ import annotations

import json
import time
from typing import Any

from pipecat.audio.utils import create_stream_resampler
from pipecat.frames.frames import (
    AudioRawFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup
from pipecat.serializers.base_serializer import FrameSerializer


class JambonzFrameSerializer(FrameSerializer):
    """Converts between Pipecat frames and the jambonz `listen` websocket."""

    class InputParams(FrameSerializer.InputParams):
        """Configuration for JambonzFrameSerializer.

        Parameters:
            jambonz_sample_rate: Override for the fork rate. Leave as None --
                the preamble states the rate the verb actually got, and a
                constant here is one more thing to keep in step with the verb
                array. Only useful if a deployment strips the preamble.
            sample_rate: Override for the pipeline input rate, which otherwise
                comes from the pipeline at setup.
            kill_audio_guard_secs: How long to withhold audio after sending
                `killAudio`. 0.1 is the top of the 50-100ms Sam Machin
                suggested; the cost is paid once per barge-in.
        """

        jambonz_sample_rate: int | None = None
        sample_rate: int | None = None
        kill_audio_guard_secs: float = 0.1

    def __init__(self, params: InputParams | None = None, **kwargs):
        """Initialize the serializer.

        Args:
            params: Configuration parameters.
            **kwargs: Passed to FrameSerializer (e.g. name).
        """
        params = params or JambonzFrameSerializer.InputParams()
        super().__init__(params, **kwargs)
        self._params: JambonzFrameSerializer.InputParams = params

        # The fork rate. None until the preamble arrives, unless overridden --
        # deliberately not defaulted to 16000, so that a missing preamble shows
        # up as "I don't know" rather than as plausible-sounding wrong audio.
        self._jambonz_sample_rate: int | None = params.jambonz_sample_rate
        self._sample_rate: int = 0  # the pipeline's rate, filled in by setup()

        # Carried from the preamble purely so a Pipecat session can be matched
        # to a jambonz call in logs. They arrive free; the alternative is
        # threading identifiers through the verb's url.
        self._call_sid: str | None = None
        self._trace_id: str | None = None
        self._call_meta: dict[str, Any] = {}

        # Wall clock, not a counter: the guard has to survive whatever the
        # pipeline is doing between calls to serialize().
        self._guard_until: float = 0.0

        self._input_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )
        self._output_resampler = create_stream_resampler(
            clear_after_secs=self._params.resampler_clear_after_secs
        )

    # ── What the preamble told us ──────────────────────────────────

    @property
    def jambonz_sample_rate(self) -> int | None:
        """The fork rate for this call, as stated by the socket."""
        return self._jambonz_sample_rate

    @property
    def call_sid(self) -> str | None:
        """jambonz's call identifier, for correlating logs."""
        return self._call_sid

    @property
    def trace_id(self) -> str | None:
        """jambonz's trace identifier, for correlating logs."""
        return self._trace_id

    @property
    def call_meta(self) -> dict[str, Any]:
        """The whole preamble, for anything this class does not name."""
        return dict(self._call_meta)

    # ── Lifecycle ──────────────────────────────────────────────────

    async def setup(self, setup: FrameProcessorSetup):
        """Take the pipeline's input rate.

        Args:
            setup: Pipeline configuration.
        """
        self._sample_rate = self._params.sample_rate or setup.audio_in_sample_rate

    # ── jambonz -> Pipecat ─────────────────────────────────────────

    async def deserialize(self, data: str | bytes) -> Frame | None:
        """Turn one websocket message into a Pipecat frame.

        A `str` is the preamble -- one JSON object, before any audio, carrying
        the geometry and the call's identity. Everything else is raw L16.

        Args:
            data: One websocket message.

        Returns:
            An ``InputAudioRawFrame`` for audio, or None for the preamble and
            for audio that cannot yet be interpreted.
        """
        if isinstance(data, str):
            self._adopt_preamble(data)
            return None

        if self._jambonz_sample_rate is None:
            # Audio before the preamble. The rate is genuinely unknown, and
            # guessing 16000 here would produce audio that sounds almost right
            # on a narrowband call -- the worst kind of wrong.
            return None

        resampled = await self._input_resampler.resample(
            data, self._jambonz_sample_rate, self._sample_rate
        )
        if not resampled:
            return None

        return InputAudioRawFrame(
            audio=resampled,
            num_channels=1,  # mixType is mono; the verb asks for it
            sample_rate=self._sample_rate,
        )

    def _adopt_preamble(self, raw: str) -> None:
        """Read the one JSON frame jambonz sends before the audio."""
        try:
            meta = json.loads(raw)
        except ValueError:
            # Not the preamble. Nothing downstream can use it, and dropping a
            # frame is better than failing a call over an unexpected message.
            return
        if not isinstance(meta, dict):
            return

        self._call_meta = meta
        self._call_sid = meta.get("callSid")
        self._trace_id = meta.get("traceId")

        rate = meta.get("sampleRate")
        if isinstance(rate, int) and rate > 0 and self._params.jambonz_sample_rate is None:
            self._jambonz_sample_rate = rate

    # ── Pipecat -> jambonz ─────────────────────────────────────────

    async def serialize(self, frame: Frame) -> str | bytes | None:
        """Turn a Pipecat frame into something to write to the socket.

        Audio goes out as ``bytes``; a ``str`` would be delivered as a TEXT
        frame and jambonz reads TEXT as control, not audio.

        Args:
            frame: The frame to send.

        Returns:
            ``bytes`` of raw L16 for audio, a JSON ``str`` for control, or None
            when there is nothing to send.
        """
        if isinstance(frame, InterruptionFrame):
            # Open the guard before returning, so audio arriving in the same
            # tick as the flush is already being held.
            self._guard_until = time.monotonic() + self._params.kill_audio_guard_secs
            return json.dumps({"type": "killAudio"})

        if isinstance(frame, AudioRawFrame):
            if time.monotonic() < self._guard_until:
                # Inside the window. Dropping rather than queueing is correct:
                # this audio belongs to the turn the caller just interrupted,
                # so playing it late is worse than not playing it.
                return None

            if self._jambonz_sample_rate is None:
                return None

            resampled = await self._output_resampler.resample(
                frame.audio, frame.sample_rate, self._jambonz_sample_rate
            )
            return resampled or None

        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            if self.should_ignore_frame(frame):
                return None
            return json.dumps(frame.message)

        return None
