# jambonz-bridge

Running the same voice agent on LiveKit Agents and on Pipecat, to document what
actually changes when you migrate between them -- and then to write the jambonz
serializer that Pipecat does not have.

Pipecat ships serializers for Twilio, Telnyx, Plivo, Exotel, Vonage and Genesys.
There is none for jambonz, so a Pipecat developer choosing a phone layer has six
options and jambonz is not one of them.

## Where it is

- **Step 1 -- done.** `step1_pipecat_local.py`: the smallest Pipecat agent that
  holds a conversation, on local audio. Same dental-clinic receptionist as the
  LiveKit agent it is being compared with, down to the two tools.
- **Step 2 -- done.** The LiveKit agent was already minimal at 77 lines, so the
  comparison is the two files side by side rather than a rewrite.
- **Step 3 -- in progress.** The concept map, written from having run both.
- **Step 4 -- started.** jambonz Cloud account, SIP client, softphone registered,
  a call traced end to end. The serializer comes next.

## What running it has turned up so far

**A LiveKit agent on `inference.*` holds no provider keys at all.** The agent
being migrated uses `inference.STT`, `inference.LLM` and `inference.TTS`, so
migrating it to Pipecat starts with obtaining Deepgram, OpenAI and Cartesia
credentials the project never needed. That belongs on page one of any migration
guide and does not appear in either framework's docs.

**Turn-taking is invisible in one and explicit in the other.** LiveKit hides it
inside `AgentSession`. Pipecat makes you place the VAD, the aggregator pair and
the turn analyser yourself -- and placing the VAD on the aggregator instead of
the transport does not raise anything. It silently fragments turns.

**Measured, same six-line test card spoken twice:** 2.2x fragmentation speaking
slowly and clearly, 1.4x at normal phone pace. Every remaining break followed a
filler word and a pause. The LiveKit agent's own log shows about 1.3x over one
conversation, so this is a voice-agent problem rather than a Pipecat one.

**Latency shape:** STT first byte ~0.5s, LLM first token 0.9-2.0s, TTS first
audio 0.44-0.73s. The LLM owns roughly two thirds of the budget.

**Tools are the difference between an answer and an invention.** With none, the
Pipecat agent invented appointment times and confirmed a booking that did not
exist -- differently on each of two runs. The LiveKit agent called its tool and
wrote a real one. Both now have the same two tools.

## The jambonz model, from a traced call

A call arrives, jambonz POSTs to the application webhook, and the webhook
returns a JSON array of verbs that jambonz executes in sequence:

    [{"verb":"pause","length":1.5},
     {"verb":"play","url":"..."},
     {"verb":"say","text":"..."}]

That is where the bridge sits: a `listen` verb forking caller audio to a
websocket, with `bidirectionalAudio` enabled for the return path, and
`killAudio` as the counterpart to Pipecat's `InterruptionFrame`. The fork rate
is set per call by the verb's `sampleRate`; 16 kHz is the default and 8 kHz is
valid for narrowband SIP.

## Prior art

`usetuner/tuner-pipecat-sdk-python` carries a `JambonzFrameSerializer` inline in
`examples/nova_clinic_pipecat/jambonz_server.py`, written because -- in their
words -- "Pipecat ships no built-in Jambonz serializer, so we provide one inline
here". It subclasses `FrameSerializer`, resamples both directions and sends raw
L16 PCM with no JSON envelope.

It is an example inside one company's SDK rather than something a Pipecat user
can import, so the gap in `pipecat/serializers/` stands. It is useful as a
reference and as evidence that the gap costs people real work.

Its caveats say the `listen` verb is unidirectional by default and suggest
`dial` with `type: "ws"` instead. The documentation says otherwise:
`bidirectionalAudio.enabled` defaults to **true**, `streaming` defaults to
false, and `dial` with a websocket type is not mentioned as a two-way audio
mechanism at all. So the verb array that file actually ships -- `listen` with
`bidirectionalAudio` -- is the documented path, and its own caveat is stale.

`killAudio` is documented as flushing "any audio that is playing out from the
bidirectional socket as well as any buffered audio". What that leaves open is
the race: whether a chunk written by the socket's other end in the same instant
is flushed with the rest or lands after it. That boundary is what Pipecat's
`InterruptionFrame` has to be built around, and it is the next thing to pin
down.

## Running step 1

    .venv\Scripts\python.exe step1_pipecat_local.py

Needs `DEEPGRAM_API_KEY` and `OPENROUTER_API_KEY` in `.env`. `test_card.md` has
the six lines to speak and how to speak them, so two runs are comparable.
