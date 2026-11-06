# jambonz Voice Bot Examples

Examples of voice bots that answer [jambonz](https://jambonz.org) calls with
[Pipecat](https://github.com/pipecat-ai/pipecat). jambonz handles the call and
streams its audio to the bot over a websocket using the
[`listen`](https://docs.jambonz.org/verbs/verbs/listen) verb; Pipecat runs the
STT, LLM and TTS pipeline and streams the reply back on the same connection.

## Examples

### 🔽 [Inbound Calling](./inbound/)

A caller dials a number (or a registered SIP device) routed to a jambonz
application, and talks to the bot.

## Architecture

```
Phone Call ↔ jambonz ↔ listen verb / WebSocket ↔ Pipecat ↔ AI Services
```

**Components:**

- **jambonz**: SIP and PSTN call handling, and the audio stream
- **`listen` verb**: forks the caller's audio to a websocket and plays back what the bot sends
- **`JambonzFrameSerializer`**: converts between jambonz's websocket messages and Pipecat frames
- **Pipecat**: audio pipeline and AI service orchestration
- **AI Services**: OpenAI (LLM), Deepgram (STT and TTS)

## The serializer

`JambonzFrameSerializer` ships with Pipecat from 1.16.0, in
`pipecat.serializers.jambonz`. It reads the call's sample rate, `callSid` and
`traceId` from the first message jambonz sends. The one setting it needs is
`audio_out_sample_rate`, which must match the `listen` verb's
`bidirectionalAudio.sampleRate`.

## Testing without a call

`tools/fake_jambonz.py` acts as jambonz against a running bot, sending the
first message, audio and a DTMF event, so the websocket path can be checked
without placing a call. See [`inbound/README.md`](./inbound/README.md#testing-without-a-call).

## Getting Help

- **Detailed Setup**: see [`inbound/README.md`](./inbound/README.md)
- **Pipecat Documentation**: [docs.pipecat.ai](https://docs.pipecat.ai)
- **jambonz Documentation**: [docs.jambonz.org](https://docs.jambonz.org)

## License

MIT. See [LICENSE](LICENSE).
