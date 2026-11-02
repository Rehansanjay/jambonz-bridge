# pipecat-jambonz

The jambonz serializer for [Pipecat](https://github.com/pipecat-ai/pipecat), with
an example bot, its tests, and the docs page.

Pipecat ships serializers for Twilio, Telnyx, Plivo, Exotel, Vonage and Genesys.
`JambonzFrameSerializer` adds jambonz to that list: it connects a Pipecat
pipeline to a jambonz call over the `listen` verb's websocket, so the agent
receives the caller's audio and speaks back on the same connection.

## The verb

```json
{
  "verb": "listen",
  "url": "wss://your-bot.example.com/ws",
  "sampleRate": 16000,
  "mixType": "mono",
  "passDtmf": true,
  "bidirectionalAudio": { "enabled": true, "streaming": true, "sampleRate": 16000 }
}
```

Three properties have to be set deliberately, and none of their defaults is the
one you want here:

- **`streaming` defaults to `false`**, and while it is false the audio your
  application sends back is discarded. Nothing is logged and no error is
  raised; the caller simply hears silence.
- **`sampleRate` defaults to `8000`.** The serializer reads whichever rate you
  chose out of the preamble, so the same agent handles a narrowband PSTN call
  and a wideband call with no code change -- but wideband has to be asked for.
- **`bidirectionalAudio.sampleRate` has no default.** Omitted, your audio is
  read at the call's negotiated codec rate, which depends on the carrier.
  jambonz resamples whatever arrives at that rate, but this serializer sends at
  the `sampleRate` it read from the preamble, so **set the two to the same
  value**. Mismatched, playback runs fast or slow by exactly that ratio.

`mixType` must be `mono`. The serializer refuses `stereo`, which would be read
as mono at double speed, and `mixed`, which is both legs summed into one channel
and so carries the agent's own audio back to it.

## The wire format, measured on a live call

The socket opens with one JSON text frame carrying the sample rate, the channel
layout and the call identifiers. Audio then arrives as binary frames of raw
16-bit PCM, a fixed 640 bytes each, which is exactly 20 ms at 16 kHz.

Text frames do not stop after the preamble. A DTMF digit arrives as
`{"event":"dtmf","dtmf":"5","duration":"1600"}` when the verb sets `passDtmf`,
and `mark` and `playDone` replies arrive on the same socket. Only the first text
frame is the preamble; the rest are events.

Once the preamble has arrived, `serializer.call_sid` and `serializer.trace_id`
are available for correlating logs with jambonz's, and `serializer.call_meta`
holds the whole preamble.

## Files

| | |
| --- | --- |
| `jambonz_serializer.py` | the serializer, byte-for-byte as submitted upstream |
| `test_jambonz_serializer.py` | its tests |
| `jambonz_bot.py` | an example bot: FastAPI call hook, websocket, Deepgram STT/TTS, an LLM |
| `docs/pipecat.mdx` | the page for docs.jambonz.org |
| `VERIFICATION.md` | five cases to run against a real call, and what counts as a pass |

Three helpers for working on it:

| | |
| --- | --- |
| `preflight.py` | checks every precondition for a live call: tunnel, verb rates, account, registration, credentials |
| `fake_jambonz.py` | drives the bot's websocket with a synthetic preamble, audio and DTMF -- no phone needed |
| `place_call.py` | rings a registered softphone from jambonz, for when dialling in is inconvenient |

## Running the example

```
ngrok http 8080
$env:PUBLIC_HOST = '<the ngrok host, no scheme>'
$env:SAMPLE_RATE = '16000'
.venv\Scripts\python.exe -u jambonz_bot.py
```

Point a jambonz application's calling webhook at `https://<host>/`, set it as
the account's device calling application, and dial from a registered softphone.
`SAMPLE_RATE` sets both rates in the verb at once, which is the pairing that
matters.

Needs `DEEPGRAM_API_KEY` and an LLM key in `.env`. Run `preflight.py` first; it
fails with the reason rather than letting a call fail silently.

No carrier is needed. A softphone restricted to G.711 gives the same 8 kHz
geometry as a PSTN leg.

## License

MIT. See [LICENSE](LICENSE).
