# Pipecat

[Pipecat](https://github.com/pipecat-ai/pipecat) is an open-source framework for voice
agents. `JambonzFrameSerializer` connects a Pipecat pipeline to a jambonz call
through the [`listen`](/verbs/verbs/listen) verb, so the bot receives caller
audio and speaks back over the same websocket.

## The verb

```json
{
  "verb": "listen",
  "url": "wss://your-bot.example.com/ws",
  "sampleRate": 16000,
  "mixType": "mono",
  "passDtmf": true,
  "bidirectionalAudio": {
    "enabled": true,
    "streaming": true,
    "sampleRate": 16000
  }
}
```

Four of those are easy to leave out and awkward to debug afterwards.

**`streaming` defaults to `false`.** While it is false, audio your application
sends back is discarded. Nothing is logged and no error is raised — the caller
simply hears silence. Set it to `true`.

**`sampleRate` defaults to `8000`.** The other allowed values are `16000`,
`24000`, `48000` and `64000`. The serializer reads whichever rate you chose out
of the preamble, so a narrowband call needs no code change; but if you want
wideband audio you have to ask for it here.

**`bidirectionalAudio.sampleRate` has no default.** Leave it out and the audio
you send is interpreted at the call's negotiated codec rate — 8 kHz for PCMU or
PCMA, higher for a wideband codec. That depends on the carrier and on what the
far end offered, so it is not something to rely on. Always set it.

jambonz does not require the two rates to be equal; it resamples whatever
arrives at `bidirectionalAudio.sampleRate`. The constraint comes from the
serializer, which sends at the `sampleRate` it read from the preamble and is
never told the return rate. **So with this serializer, set
`bidirectionalAudio.sampleRate` to the same value as `sampleRate`.** If they
differ, playback runs fast or slow by exactly that ratio.

**`mixType` defaults to `mono`, which is what you want.** `stereo` and `mixed`
carry two channels, and the serializer refuses that audio rather than
misreading interleaved stereo as mono — which would play at double speed with
the channels alternating. If you see `Dropping audio: jambonz is sending
mixType=...` in your logs, that is this.

## A minimal bot

```python
import os

from fastapi import FastAPI, WebSocket

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.jambonz import JambonzFrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

app = FastAPI()


@app.websocket("/ws")
async def jambonz_call(websocket: WebSocket):
    await websocket.accept()

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=JambonzFrameSerializer(),
        ),
    )

    context = LLMContext(
        [{"role": "system", "content": "You are a receptionist. Keep replies to one sentence."}]
    )
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer()),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"]),
            user_aggregator,
            OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"]),
            CartesiaTTSService(
                api_key=os.environ["CARTESIA_API_KEY"],
                voice_id=os.environ["CARTESIA_VOICE_ID"],
            ),
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))
    runner = WorkerRunner()
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await worker.queue_frames([LLMRunFrame()])

    await runner.run()
```

The serializer needs no configuration. The rate, the channel layout and the
call's identifiers all come from the preamble jambonz sends before any audio,
so the same bot handles a narrowband PSTN call and a wideband softphone call
without a change.

`serializer.call_sid` and `serializer.trace_id` are available once the
preamble has arrived, for correlating your logs with jambonz's. The whole
preamble is on `serializer.call_meta`.

## DTMF

With `passDtmf: true`, a digit arrives as a text frame on the same socket and
becomes a Pipecat `InputDTMFFrame`:

```json
{ "event": "dtmf", "dtmf": "5", "duration": "1600" }
```

Handle it like any other frame:

```python
from loguru import logger

from pipecat.frames.frames import InputDTMFFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


class CollectDigits(FrameProcessor):
    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, InputDTMFFrame):
            logger.info(f"caller pressed {frame.button}")
        await self.push_frame(frame, direction)
```

`mark` and `playDone` replies arrive on the same socket and are ignored.

## Ending the call

The bot can hang up. When the pipeline ends, the serializer sends
`disconnect`, which ends the `listen` verb — after which jambonz runs the
verb's `actionHook` if it has one, and otherwise hangs up the call. Nothing
else is needed, and unlike Twilio there are no API credentials to configure.

```python
from pipecat.frames.frames import EndFrame

await worker.queue_frames([EndFrame()])
```

Turn it off with `JambonzFrameSerializer.InputParams(disconnect_on_end=False)`
if your application ends the verb itself, or if the call should outlive the
pipeline.

## Interruptions

When the caller talks over the bot, Pipecat raises an interruption and the
serializer sends `killAudio`, which flushes playing and buffered audio.
Nothing further is required: jambonz reads the socket in order, so the next
turn's audio queues behind the flush rather than racing it.

## Trying it

A softphone is enough — no carrier needed.

1. Expose the bot on a public `wss` URL. During development, `ngrok http 8000`
   and use `wss://<id>.ngrok.app/ws` as the verb's `url`.
2. Point your jambonz application's call webhook at an endpoint that returns
   the verb above, and set it as the account's device calling application.
3. Register a softphone against your jambonz account and dial.

To test the narrowband path, set the softphone's codec to PCMU or PCMA and set
both rates in the verb to `8000`. That is the same audio geometry as a G.711
PSTN carrier.
