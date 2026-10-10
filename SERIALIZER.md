# The jambonz serializer: what it has to do

Notes for writing `JambonzFrameSerializer`, taken from reading Pipecat's base
class, the Exotel serializer it would sit beside, jambonz's own `listen` verb
documentation, and the one inline implementation in the wild.

## The interface

`pipecat/serializers/base_serializer.py` -- `FrameSerializer(BaseObject)`:

    async def setup(self, setup: FrameProcessorSetup)   # optional
    async def serialize(self, frame: Frame) -> str | bytes | None      # abstract
    async def deserialize(self, data: str | bytes) -> Frame | None     # abstract

Two abstract methods. `serialize` turns a Pipecat frame into something to put
on the websocket; `deserialize` turns bytes off the websocket into a frame.

## How Exotel does it, in 171 lines

Worth copying the shape, not the content.

- A nested `InputParams` dataclass carrying `exotel_sample_rate: int = 8000`
  and an optional `sample_rate` override for the pipeline side.
- Two resamplers built in `__init__` from `create_stream_resampler(...)`: one
  for each direction. Not one shared instance.
- `setup()` reads the pipeline's rate from `setup.audio_in_sample_rate`, so the
  serializer learns it rather than assuming it.
- `serialize()` branches on frame type:
  - `InterruptionFrame` -> `{"event": "clear", "stream_sid": ...}` as JSON
  - an audio frame -> resample to the provider rate, base64, wrap in JSON
  - a transport message -> `json.dumps(frame.message)`
- `deserialize()` parses JSON, pulls `message["media"]["payload"]`, base64
  decodes, resamples to the pipeline rate, returns an `InputAudioRawFrame`.

## Where jambonz differs

**The audio path is simpler.** Exotel wraps every chunk in base64 inside a JSON
envelope. jambonz sends raw binary L16 PCM on the socket -- 16-bit signed
little-endian, mono, no envelope. So `serialize` returns `bytes` for audio
rather than a JSON string, and `deserialize` takes the bytes straight to the
resampler with no parsing step.

**The rate is not fixed.** Exotel hardcodes a default of 8000. jambonz sets the
fork rate per call through the `listen` verb's `sampleRate`, with 16 kHz the
default and 8 kHz valid for narrowband SIP. So the jambonz params should take
the rate rather than assume one, and the verb array and the serializer have to
agree.

**The control path shares the socket.** This is the real design question.
Exotel's `clear` goes out as JSON on the same connection that carries base64
audio, so there is no ambiguity about ordering -- it is all one stream of JSON
messages. On jambonz the audio is raw binary, so a `killAudio` command is a
different kind of message on the same socket. `InterruptionFrame` has to become
that command, and the two kinds of message have to be distinguishable at both
ends.

## What the documentation settles

- `bidirectionalAudio.enabled` defaults to **true**; `streaming` defaults to
  false. `listen` is not unidirectional.
- `dial` with a websocket type is not documented as a two-way audio mechanism.
- `killAudio` flushes "any audio that is playing out from the bidirectional
  socket as well as any buffered audio".

## What the documentation does not settle, answered in the community

Whether a chunk written from our end at the same moment `killAudio` is
processed is flushed with the rest, or lands after it and plays over the
caller. Asked in the jambonz community Slack on 7 Oct 2026; Sam Machin
answered from operational experience:

> the ordering of messages on the socket isn't 100%, we've seen clients that
> sometimes prioritise smaller messages so its best to have a small break
> after a killAudio before you send the next stream, 50-100ms should be plenty

So **ordering is not guaranteed**, and the cause is client-side message
prioritisation rather than anything tunable on our end. A small control
message can be reordered relative to larger audio frames.

### What that makes the serializer do

On `InterruptionFrame` it is not enough to send `killAudio` and resume. The
serializer has to send it and then **refuse to write audio for 50-100ms**,
which means carrying state: `serialize()` will be called again with the next
bot audio well inside that window and has to hold it.

Pipecat's interruption model assumes a processor can simply stop. Over jambonz
it has to stop *and wait*, so the guard belongs in the serializer rather than
anywhere upstream of it.

The cost is 50-100ms added to every barge-in recovery, against a
first-audio budget measured here at 0.44-0.73s. Worth stating in the guide
rather than discovering in production.

## What a live call settles

A 30-second call from a registered softphone through jambonz Cloud, forked to
`probe_jambonz_socket.py` with `listen` asking for 16 kHz. 11 October 2026.

    binary frames : 1514
    total bytes   : 968960
    frame sizes   : [640]
    text frames   : 1
    wall clock    : 30.03s
    audio implied : 30.28s at 16000 Hz, 16-bit mono
    ratio         : 1.01

**The rate the verb asks for is the rate that arrives.** Ratio 1.01 across
thirty seconds. Worth stating because the opposite was assumed here first, got
written into the README, and was wrong: this project claimed jambonz forks at
8 kHz. It does not. It forks at whatever `sampleRate` the verb set, and the
default is 16 kHz.

**Framing is fixed at 20 ms.** One distinct frame size across 1514 frames:
640 bytes, which is 320 samples of 16-bit mono, which is exactly 20 ms at
16 kHz. `deserialize` needs no variable-length path.

**The control path does not share the socket -- the metadata does, once.**
This answers the design question above, and the answer is better than feared.
Exactly one TEXT frame arrives, *before any audio*, and then the socket is pure
binary for the rest of the call:

    {"sampleRate":16000,"mixType":"mono","callSid":"3e5d869a-...",
     "direction":"inbound","from":"rehan","to":"1234","sipStatus":200,
     "sipReason":"OK","callStatus":"in-progress","accountSid":"87f4ab51-...",
     "traceId":"ccb228b2..."}

It is a preamble, not a channel. So `deserialize` does not branch per frame
between JSON and PCM. It reads one metadata frame at the start and treats
everything after it as L16.

### What that makes the serializer do

Take the rate **from the preamble**, not from `InputParams`. The verb and the
serializer were supposed to "agree" on a rate; they do not have to, because the
socket states it. A params field can still override for a narrowband carrier,
but the wire is the source of truth and the default should be to believe it.

The preamble also carries `callSid`, `from`, `to` and `traceId`, which is
everything needed to correlate a Pipecat session with a jambonz call in logs
without threading identifiers through the verb's `url`.

Note the shape is not unique to jambonz. Cosmo's realtime SDK opens its
websocket with a `ws-audio-format` frame carrying the same three facts --
input rate, output rate, channel count -- before any audio. Two unrelated
vendors arrived at the same preamble design, which is an argument for the
serializer treating it as the expected pattern rather than a jambonz quirk.

## What writing it turned up

**Pipecat's stream resampler does not emit one chunk per chunk.** It buffers and
flushes in bursts. Measured on `create_stream_resampler`, 16 kHz to 24 kHz, in
640-byte frames:

    chunk 1: in 640 -> out 0
    chunk 2: in 640 -> out 0
    chunk 3: in 640 -> out 0
    chunk 4: in 640 -> out 3300

So `serialize` and `deserialize` return `None` most of the time and a large
buffer occasionally, and neither the serializer nor anything testing it may
assume a frame in means a frame out. The first version of the tests here did
assume it and failed against a correct serializer -- worth saying plainly,
because the failure looks like a bug in the code under test.

Over 20 frames the throughput settles to about 86% of the arithmetic ideal, the
remainder being the tail still held in the buffer. Tests assert a band rather
than an exact byte count for that reason.

This is also why `InputParams.resampler_clear_after_secs` matters on a telephony
transport: the base default clears resampler history after 0.2s of silence, and
a caller who pauses mid-sentence can exceed that. Genesys sets it to `None` for
exactly this reason, and jambonz has the same irregular-gap problem.

## Prior art

`usetuner/tuner-pipecat-sdk-python`, `examples/nova_clinic_pipecat/jambonz_server.py`:
288 lines, a `JambonzFrameSerializer` inline, written because "Pipecat ships no
built-in Jambonz serializer". Useful as a reference for the raw-PCM handling.
Its caveat claiming `listen` is unidirectional does not match the current docs.

Credit it when this gets upstreamed.
