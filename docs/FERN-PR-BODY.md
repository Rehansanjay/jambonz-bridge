Adds a Pipecat page under Tutorials -> Voice AI Examples.

Pipecat is a widely used open-source voice-agent framework, and the companion
piece on the Pipecat side -- a `JambonzFrameSerializer` -- is in review at
pipecat-ai/pipecat#6112. This page is the jambonz half: what the `listen` verb
has to look like, and the three properties that have to be set together.

The reason the page exists is that none of those three defaults is the one you
want for this:

- `bidirectionalAudio.streaming` defaults to `false`, and while it is false the
  audio the application sends back is discarded. Nothing is logged, no error is
  raised, and the caller hears silence.
- `bidirectionalAudio.sampleRate` has no default, so omitted, the returning
  audio is read at the call's negotiated codec rate. If that differs from
  `sampleRate`, playback runs fast or slow by exactly that ratio.
- `sampleRate` itself defaults to `8000`, which is right for a PSTN leg and
  surprising if you expected wideband.

The page covers the verb, those three properties and `mixType`, a minimal
agent, how DTMF arrives, how the agent ends the call, what happens on an
interruption, and a softphone recipe for trying it without a carrier.

**Placement is a judgement call.** "Voice AI Examples" currently holds
speech-to-speech vendors -- AssemblyAI, Deepgram, ElevenLabs, Gemini, OpenAI,
Qwen, xAI, Ultravox. Pipecat is a framework rather than a vendor, so it is the
odd one out there. The alternative is Guides -> Features beside `Voice Agents`,
which documents the `agent` verb; that page is about jambonz orchestrating the
turn cycle itself, where this one is about handing that job to an external
framework. I put it in Tutorials because it is a worked integration example
with runnable code, which is what that tab is for -- but it is your site, and
I am happy to move it.
