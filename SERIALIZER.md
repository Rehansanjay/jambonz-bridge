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

## What it does not settle

Whether a chunk written from our end of the socket at the same moment
`killAudio` is processed is flushed with the rest, or lands after it and plays
over the caller. That boundary decides how `InterruptionFrame` must be handled:
if there is no guarantee, the serializer has to stop writing before sending
`killAudio` and wait for something, rather than assuming the flush covers it.

This is the one open question worth asking rather than guessing.

## Prior art

`usetuner/tuner-pipecat-sdk-python`, `examples/nova_clinic_pipecat/jambonz_server.py`:
288 lines, a `JambonzFrameSerializer` inline, written because "Pipecat ships no
built-in Jambonz serializer". Useful as a reference for the raw-PCM handling.
Its caveat claiming `listen` is unidirectional does not match the current docs.

Credit it when this gets upstreamed.
