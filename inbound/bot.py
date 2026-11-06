#
# Copyright (c) 2026, Rehan Sanjay Venkatesan
#
# SPDX-License-Identifier: MIT
#

import os

from dotenv import load_dotenv
from fastapi import Request
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.runner.run import app
from pipecat.runner.types import WebSocketRunnerArguments
from pipecat.serializers.jambonz import JambonzFrameSerializer
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

# 8000 for narrowband (PSTN), 16000 for wideband. Used for both directions:
# the rate jambonz sends the caller's audio at, and the rate it reads the bot's
# audio at.
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))


@app.post("/")
async def call_hook(request: Request):
    """jambonz calling webhook: connect the call to the bot's websocket."""
    host = request.headers["host"]
    return [
        {
            "verb": "listen",
            "url": f"wss://{host}/ws",
            "sampleRate": SAMPLE_RATE,
            "mixType": "mono",
            "passDtmf": True,
            "bidirectionalAudio": {
                "enabled": True,
                # Without streaming, jambonz discards the audio the bot sends back.
                "streaming": True,
                # Must equal sampleRate, or playback runs fast or slow.
                "sampleRate": SAMPLE_RATE,
            },
        }
    ]


@app.post("/call-status")
async def call_status(request: Request):
    """jambonz call status webhook."""
    payload = await request.json()
    logger.info(f"Call {payload.get('call_sid')}: {payload.get('call_status')}")
    return {}


async def run_bot(transport: BaseTransport, handle_sigint: bool):
    llm = OpenAILLMService(
        api_key=os.getenv("OPENAI_API_KEY"),
        settings=OpenAILLMService.Settings(
            system_instruction="You are a helpful assistant on a phone call. Keep replies short. Your output will be converted to audio so don't include special characters in your answers.",
        ),
    )

    stt = DeepgramSTTService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    tts = DeepgramTTSService(api_key=os.getenv("DEEPGRAM_API_KEY"))

    context = LLMContext()
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),  # Websocket input from jambonz
            stt,  # Speech-To-Text
            user_aggregator,
            llm,  # LLM
            tts,  # Text-To-Speech
            transport.output(),  # Websocket output to jambonz
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    runner = WorkerRunner(handle_sigint=handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        # Kick off the conversation.
        context.add_message({"role": "developer", "content": "Greet the caller briefly."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await runner.cancel()

    await runner.run()


async def bot(runner_args: WebSocketRunnerArguments):
    """Main bot entry point, called by the Pipecat runner for each call."""

    # The serializer reads the caller's audio rate, callSid and traceId from the
    # first message jambonz sends, and handles DTMF, marks and killAudio. The rate
    # jambonz reads the bot's audio at isn't in that message, so it is set here
    # to the verb's bidirectionalAudio.sampleRate.
    serializer = JambonzFrameSerializer(
        JambonzFrameSerializer.InputParams(audio_out_sample_rate=SAMPLE_RATE)
    )

    transport = FastAPIWebsocketTransport(
        websocket=runner_args.websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            serializer=serializer,
        ),
    )

    await run_bot(transport, runner_args.handle_sigint)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
