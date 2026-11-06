# jambonz Chatbot: Inbound

A Pipecat voice bot that answers calls from jambonz. The bot serves two things
from one process:

- **`POST /`**, the jambonz calling webhook. It answers every call with a
  `listen` verb pointing back at this server's websocket.
- **`/ws`**, the websocket jambonz then connects to. Each connection runs one
  Pipecat pipeline for the duration of the call.

## Prerequisites

### jambonz

- A [jambonz.cloud](https://jambonz.cloud) account, or a self-hosted jambonz
- One way to place a call to it:
  - a phone number from a carrier, routed to your application, or
  - a SIP softphone registered to your account, with the application set as
    the account's device calling application

### AI Services

- OpenAI API key for the LLM
- Deepgram API key for speech-to-text and text-to-speech

### System

- Python 3.11+
- `uv` package manager
- ngrok (for local development)

## Setup

1. Set up a virtual environment and install dependencies:

   ```sh
   cd inbound
   uv sync
   ```

2. Create an .env file and add API keys:

   ```sh
   cp env.example .env
   ```

## Local development

### Start ngrok

jambonz has to reach the bot, so it needs a public address. In a new terminal:

```sh
ngrok http 7860
```

> Tip: Use the `--url` flag for a reusable ngrok URL, so the webhooks below
> don't need changing every time ngrok restarts.

### Configure a jambonz application

1. In the jambonz portal, go to **Applications** and add one:
   - **Calling webhook**: `https://your-url.ngrok.app/`, method POST
   - **Call status webhook**: `https://your-url.ngrok.app/call-status`, method POST
   - Speech vendors don't matter here; the bot brings its own.
2. Route calls to it, in either of these ways:
   - **Phone number**: under **Phone Numbers**, set the number's application
     to the one you just created.
   - **SIP softphone**: under **Clients**, add a SIP client and register a
     softphone with it. Then, under **Account**, set the device calling
     application to the one you just created.

### Run your bot

```sh
uv run bot.py
```

Call the number, or dial any number from the softphone. The bot answers and
greets you.

## The `listen` verb

The calling webhook returns:

```json
{
  "verb": "listen",
  "url": "wss://your-url.ngrok.app/ws",
  "sampleRate": 16000,
  "mixType": "mono",
  "passDtmf": true,
  "bidirectionalAudio": { "enabled": true, "streaming": true, "sampleRate": 16000 }
}
```

Three of these have defaults that don't work for a bot:

- **`bidirectionalAudio.streaming` defaults to `false`.** While it is false,
  jambonz discards the audio the bot sends back. Nothing is logged; the caller
  hears silence.
- **`sampleRate` defaults to `8000`.** Ask for `16000` to get wideband audio
  from a wideband call. The serializer reads this rate from the call.
- **`bidirectionalAudio.sampleRate` has no default.** It is the rate jambonz
  reads the bot's audio at, and it isn't reported on the websocket, so the bot
  passes the same value to the serializer as `audio_out_sample_rate`. If the
  two differ, the bot's speech plays fast or slow.

`SAMPLE_RATE` in `.env` sets all three.

`mixType` must be `mono`. The serializer rejects `stereo` and `mixed`.

## Testing without a call

With the bot running, in another terminal:

```sh
uv run python ../tools/fake_jambonz.py
```

It connects to `ws://localhost:7860/ws` the way jambonz does, sends three
seconds of tone and a DTMF digit, and reports what the bot sent back.
