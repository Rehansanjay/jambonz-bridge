"""Step 1 of the bridge: the smallest Pipecat agent that holds a conversation.

Runs on your laptop's microphone and speakers. No telephony, no jambonz, no
LiveKit. The only point of this file is to see a Pipecat pipeline work so the
migration in step 3 is a comparison rather than a guess.

Read the pipeline at the bottom first. That list IS the framework: frames go in
at the top, each processor gets a turn, and audio comes out. LiveKit hides the
same sequence inside AgentSession; here it is written out.

    .venv\\Scripts\\python.exe step1_pipecat_local.py

Talk to it. Then interrupt it mid-sentence and watch what the logs say.
"""

import asyncio
import os
import sys

from dotenv import load_dotenv
from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.observers.user_bot_latency_observer import UserBotLatencyObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker, ProcessorUnusablePolicy
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.workers.runner import WorkerRunner

load_dotenv(override=True)

logger.remove(0)
logger.add(sys.stderr, level="DEBUG")

SYSTEM_PROMPT = (
    "You are a receptionist for a small dental clinic. You are speaking on the "
    "phone, so keep answers to one or two short sentences, never use lists or "
    "symbols, and say numbers as words. If you do not know something, say so."
)


# The same two tools the LiveKit agent has, with the same canned answers, so the
# two agents are comparable. Without them this one invented appointment times
# and confirmed a booking that did not exist -- differently on each run.
#
# Note the shape, because this is the part of a migration that surprises people.
# LiveKit's @function_tool decorator is the declaration AND the implementation,
# and the handler returns a string. Here the handler takes one FunctionCallParams
# and reports through params.result_callback instead of returning, and the
# declaration is a FunctionSchema that travels with the context.


async def check_appointment_slots(params):
    """Report the free slots for a day."""
    day = params.arguments["day"]
    logger.info("TOOL check_appointment_slots day={}", day)
    await params.result_callback(f"On {day} there are free slots at ten AM and three PM.")


async def book_appointment(params):
    """Record a booking."""
    day = params.arguments["day"]
    time = params.arguments["time"]
    name = params.arguments["name"]
    logger.info("TOOL book_appointment BOOKED {} on {} at {}", name, day, time)
    await params.result_callback(f"Booked {name} on {day} at {time}.")


# A FunctionSchema can carry its own handler. When it does, the LLM service
# registers it automatically for any context that advertises the schema, so
# there is no separate register_function call to forget.
CLINIC_TOOLS = ToolsSchema(
    standard_tools=[
        FunctionSchema(
            name="check_appointment_slots",
            description=(
                "Look up free appointment slots. Call this only when the caller "
                "asks when they can book."
            ),
            properties={
                "day": {
                    "type": "string",
                    "description": 'The day the caller asked about, for example "Monday"',
                }
            },
            required=["day"],
            handler=check_appointment_slots,
        ),
        FunctionSchema(
            name="book_appointment",
            description=(
                "Book an appointment. Call this only after the caller has picked a "
                "day and time and told you their name. Never say an appointment is "
                "booked without calling this."
            ),
            properties={
                "day": {"type": "string", "description": 'The day, for example "Friday"'},
                "time": {"type": "string", "description": 'The time, for example "3 PM"'},
                "name": {"type": "string", "description": "The caller's name"},
            },
            required=["day", "time", "name"],
            handler=book_appointment,
        ),
    ]
)


async def main():
    # The transport is the only piece that changes in step 3. Here it is the
    # laptop's own microphone and speakers; later it becomes jambonz, and the
    # rest of this file stays almost exactly as it is.
    # The VAD belongs here, on the transport, because this is where microphone
    # audio arrives. It is the only place that can tell "he stopped" apart from
    # "he paused to think". Attached anywhere downstream it only sees text, by
    # which point the turn has already been cut.
    #
    # stop_secs is raised to 1.0 on purpose. Deepgram does its own endpointing
    # and finalises a transcript around 900 ms after speech ends; Silero at
    # 1.0 s fires later. The two timers run independently, so the transcript
    # lands first. That ordering is the condition pipecat-ai/pipecat#5921
    # reports against Azure, and the whole point of this run is to see whether
    # it reproduces on a second self-endpointing STT.
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=1.0)),
        )
    )

    stt = DeepgramSTTService(api_key=os.environ["DEEPGRAM_API_KEY"])

    tts = DeepgramTTSService(api_key=os.environ["DEEPGRAM_API_KEY"])

    # OpenRouter speaks the OpenAI protocol, so the OpenAI service works with a
    # different base_url. Swap the model for anything OpenRouter lists.
    llm = OpenAILLMService(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        settings=OpenAILLMService.Settings(system_instruction=SYSTEM_PROMPT),
    )

    # The context is the conversation so far. The aggregator pair is what turns
    # a stream of transcript fragments into "the user finished saying this". It
    # decides where a turn ends, but it can only do that with the transport's
    # VAD telling it when speech is still in progress.
    context = LLMContext(tools=CLINIC_TOOLS)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(context)

    # Read this top to bottom: it is the whole agent.
    pipeline = Pipeline(
        [
            transport.input(),    # microphone in
            stt,                  # audio  -> text
            user_aggregator,      # fragments -> a finished user turn
            llm,                  # user turn -> reply tokens
            tts,                  # reply tokens -> audio
            transport.output(),   # speakers out
            assistant_aggregator, # remember what was actually spoken
        ]
    )

    # The worker only builds a latency observer of its own when tracing is on,
    # so make one here and read its breakdown directly.
    latency = UserBotLatencyObserver()

    @latency.event_handler("on_latency_breakdown")
    async def _on_latency_breakdown(observer, breakdown):
        # turn_contribution_lines() names every part of the gap between the
        # user stopping and the bot speaking. The line to look for is
        # "transcription": if it is absent while the others are present, the
        # observer dropped the STT event because it arrived before
        # VADUserStoppedSpeakingFrame, which is #5921 on Deepgram.
        lines = breakdown.turn_contribution_lines()
        stages = [line.split()[1] for line in lines if len(line.split()) > 1]
        logger.info("LATENCY BREAKDOWN -- stages seen: {}", stages)
        for line in lines:
            logger.info("  {}", line)
        if not any("transcription" in line for line in lines):
            logger.warning("NO transcription STAGE IN THIS BREAKDOWN -- #5921 condition")

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        observers=[latency],
        processor_unusable_policy=ProcessorUnusablePolicy.END,
    )

    runner = WorkerRunner()
    await runner.add_workers(worker)

    # Make it speak first, the way a receptionist answering a call would.
    context.add_message({"role": "developer", "content": "Greet the caller briefly."})
    await worker.queue_frames([LLMRunFrame()])

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
