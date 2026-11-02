r"""The end-to-end test: a real Pipecat bot answering a real jambonz call.

The unit tests exercise the serializer in isolation. This is the whole path at
once, which is the only thing that proves it: jambonz
answers a call, forks the audio here, a Pipecat pipeline listens and replies,
and the caller hears it.

It imports `jambonz_serializer.py`, which is a byte-for-byte copy of the file
in pipecat-ai/pipecat#6112. So a result from this run is a result about the code
under review, not about a local variant of it.

Two endpoints, same as the probe. POST / is the call hook: jambonz asks what to
do and this answers with one `listen` verb pointing at /ws. The websocket then
carries the call.

    .venv\Scripts\python.exe -m pip install -e . ; # nothing to install, see README
    ngrok http 8080
    set PUBLIC_HOST=<the ngrok host, no scheme>
    .venv\Scripts\python.exe -u jambonz_bot.py

Then in the jambonz portal: an application whose calling webhook is
https://<PUBLIC_HOST>/ , set as the account's device calling application. Dial
from a registered softphone.

`SAMPLE_RATE` sets both rates in the verb at once, which is the whole point of
the narrowband case: run it at 8000 with the softphone on PCMU and the audio
geometry is the same as a G.711 PSTN carrier, without needing a carrier.

Needs `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY` in `.env`. Run `preflight.py`
first; it checks every precondition and fails with the reason rather than letting
a call fail silently.

Press `#` on the keypad to make the bot hang up. That is not a feature anyone
would ship; it is here because "the bot ends the call" needs a trigger that
fires on demand rather than when a language model decides, so the behaviour can
be recreated on request.

The -u matters if you redirect this to a file. Python block-buffers stdout to a
pipe, so without it the log you wanted to keep writes nothing until the process
ends.
"""

import asyncio
import json
import os

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket
from loguru import logger

from jambonz_serializer import JambonzFrameSerializer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputDTMFFrame,
    InterruptionFrame,
    LLMRunFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

PORT = int(os.getenv("PORT", "8080"))
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "")  # ngrok host, no scheme
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))
HANGUP_DIGIT = os.getenv("HANGUP_DIGIT", "#")
LOG_FILE = os.getenv("LOG_FILE", "")

SYSTEM_PROMPT = (
    "You are the receptionist at a dental clinic, answering the phone. "
    "Keep every reply to one short sentence. Never read out digits or symbols."
)

app = FastAPI()


def listen_verb() -> list[dict]:
    """The verb jambonz is told to run. This exact JSON goes in the PR."""
    return [
        {
            "verb": "listen",
            "url": f"wss://{PUBLIC_HOST}/ws",
            "sampleRate": SAMPLE_RATE,
            "mixType": "mono",
            "passDtmf": True,
            "bidirectionalAudio": {
                "enabled": True,
                "streaming": True,
                # Deliberately the same value as sampleRate. The serializer
                # sends at the rate it read from the preamble and is never told
                # the return rate, so a different value here plays back fast or
                # slow by exactly that ratio.
                "sampleRate": SAMPLE_RATE,
            },
        }
    ]


@app.post("/")
async def call_hook(request: Request) -> list[dict]:
    """What jambonz asks when a call arrives."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    logger.info(
        "call hook: {}",
        {k: payload.get(k) for k in ("call_sid", "from", "to", "direction", "sip_status")},
    )
    verb = listen_verb()
    logger.info("answering with: {}", json.dumps(verb))
    return verb


@app.post("/call-hook")
async def call_status(request: Request) -> dict:
    """Where the application's call status webhook points.

    jambonz posts the call's progress here -- trying, in-progress, completed --
    independently of the websocket. It is worth having for two reasons: the
    route existed in the portal before it existed in this file, so every call
    was answered with a 404, and `completed` carries the reason a call ended,
    which the websocket cannot report when the call never got that far.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    logger.info(
        "call status: {}",
        {
            k: payload.get(k)
            for k in ("call_status", "sip_status", "sip_reason", "call_sid", "duration")
            if payload.get(k) is not None
        },
    )
    return {}


class Narrate(FrameProcessor):
    """Print the five things the PR has to show evidence of.

    The serializer logs some of this at debug level, but a reviewer reading a
    pasted log should not have to know which logger to turn up. This states each
    event plainly, in the order it happened, so the log is the evidence.
    """

    def __init__(self, serializer: JambonzFrameSerializer, on_hangup):
        super().__init__()
        self._serializer = serializer
        self._on_hangup = on_hangup
        self._announced = False
        self._audio_frames = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        # The preamble has landed by the time the first audio arrives.
        if not self._announced and self._serializer.jambonz_sample_rate is not None:
            self._announced = True
            logger.info(
                "PREAMBLE: rate={} mixType={} callSid={}",
                self._serializer.jambonz_sample_rate,
                self._serializer.mix_type,
                self._serializer.call_sid,
            )

        if isinstance(frame, InputDTMFFrame):
            logger.info("DTMF: caller pressed {}", frame.button.value)
            if frame.button.value == HANGUP_DIGIT:
                logger.info("DTMF {} means hang up: queueing EndFrame", HANGUP_DIGIT)
                await self._on_hangup()
        elif isinstance(frame, InterruptionFrame):
            logger.info("INTERRUPTION: killAudio goes out on the socket")

        await self.push_frame(frame, direction)


@app.websocket("/ws")
async def jambonz_call(websocket: WebSocket):
    """One call. jambonz connects here after the call hook returns the verb."""
    await websocket.accept()
    logger.info("websocket open; expecting the preamble then binary audio")

    serializer = JambonzFrameSerializer()

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            # 1.0s rather than the 0.8s default: a caller on a phone pauses
            # mid-sentence more than someone at a microphone does.
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=1.0)),
            serializer=serializer,
        ),
    )

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])
    tts = DeepgramTTSService(api_key=os.environ["DEEPGRAM_API_KEY"])
    llm = OpenAILLMService(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        settings=OpenAILLMService.Settings(system_instruction=SYSTEM_PROMPT),
    )

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    worker: PipelineWorker | None = None

    async def hang_up():
        # EndFrame rather than closing the socket: the serializer turns it into
        # `disconnect`, which is what ends the listen verb on jambonz's side.
        if worker is not None:
            await worker.queue_frames([EndFrame()])

    narrate = Narrate(serializer, hang_up)

    pipeline = Pipeline(
        [
            transport.input(),     # caller audio off the socket
            narrate,               # evidence, and the hangup trigger
            stt,                   # audio -> text
            user_aggregator,       # fragments -> a finished user turn
            llm,                   # user turn -> reply tokens
            tts,                   # reply tokens -> audio
            transport.output(),    # back out on the socket
            assistant_aggregator,  # remember what was spoken
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        context.add_message({"role": "developer", "content": "Greet the caller briefly."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info("websocket closed; call over")

    await runner.run()


def main() -> None:
    if not PUBLIC_HOST:
        raise SystemExit(
            "Set PUBLIC_HOST to the ngrok host with no scheme, e.g. "
            "set PUBLIC_HOST=1a2b3c.ngrok.app"
        )
    # The log is the evidence that goes on the PR, so write a plain copy to a
    # file as well as the console. Redirecting this process's stderr from
    # PowerShell is not equivalent: 5.1 wraps every line of a native command's
    # stderr in an ErrorRecord, and the console sink's colour codes survive the
    # redirect and make the result unquotable.
    log_path = LOG_FILE or f"run-{SAMPLE_RATE}hz.log"
    logger.add(log_path, level="DEBUG", colorize=False, backtrace=False, enqueue=True)

    logger.info("log file      {}", log_path)
    logger.info("listening on :{}", PORT)
    logger.info("call hook     https://{}/", PUBLIC_HOST)
    logger.info("websocket     wss://{}/ws", PUBLIC_HOST)
    logger.info("verb rate     {} Hz, both directions", SAMPLE_RATE)
    logger.info("hangup digit  {}", HANGUP_DIGIT)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
