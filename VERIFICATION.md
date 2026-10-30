# End-to-end verification

Five cases, on a real jambonz call, against the serializer in
[pipecat-ai/pipecat#6112](https://github.com/pipecat-ai/pipecat/pull/6112).
`jambonz_serializer.py` in this repo is a byte-for-byte copy of the file in that
PR, so a result here is a result about the code under review.

No carrier is needed. A softphone forced to PCMU gives the same audio geometry
as a G.711 PSTN leg -- 8 kHz, 16-bit mono -- which is the thing the narrowband
case is actually testing.

## Setup, once

Two terminals.

**Terminal 1** — the tunnel. jambonz Cloud has to reach the bot, so it needs a
public address:

```
ngrok http 8080
```

Copy the host out of the forwarding line, without the scheme:
`1a2b3c.ngrok.app`.

**Terminal 2** — the bot:

```
set PUBLIC_HOST=1a2b3c.ngrok.app
set SAMPLE_RATE=16000
.venv\Scripts\python.exe -u jambonz_bot.py > run-wideband.log 2>&1
```

Redirect to a file for every run. The log *is* the evidence, and scrolling a
console back is how a detail gets lost.

**In the jambonz portal**, once:

1. An application whose **calling webhook** is `https://1a2b3c.ngrok.app/`,
   method POST.
2. Account **Settings → Device calling application** pointing at it.
3. A SIP client registered, with credentials for the softphone.

The ngrok host changes every time ngrok restarts, so the webhook has to be
updated whenever it does. Leave ngrok running for all five cases.

## What to capture for every case

- the verb JSON the bot answered with — the log prints it on every call, after
  `answering with:`
- the `PREAMBLE:` line, which shows the rate, the mixType and the callSid
  jambonz actually negotiated
- whether you heard the bot, and whether it sounded fast, slow or normal
- any `WARNING` or `ERROR` line

Speed is the symptom to listen for. If the two rates disagree, playback runs
fast or slow by exactly that ratio — a 16 kHz bot on an 8 kHz return path is
unmistakably chipmunk.

---

## 1. Narrowband, G.711

The case a real PSTN carrier produces.

**Softphone:** set the audio codec to **PCMU** only (G.711 µ-law). Disable
Opus, G722 and everything else, so there is nothing to negotiate up to.

**Bot:**

```
set SAMPLE_RATE=8000
.venv\Scripts\python.exe -u jambonz_bot.py > run-g711.log 2>&1
```

Dial, let the bot greet you, say *"what are your opening hours"*, listen to the
reply, hang up from the softphone.

**Pass:** `PREAMBLE: rate=8000 mixType=mono callSid=...`, the bot is
intelligible and plays at normal speed, no warnings.

**If it is fast or slow:** the two rates in the verb disagree. Both come from
`SAMPLE_RATE`, so check the `answering with:` line actually shows 8000 twice.

## 2. Wideband

**Softphone:** re-enable Opus or G722 and put it first.

**Bot:**

```
set SAMPLE_RATE=16000
.venv\Scripts\python.exe -u jambonz_bot.py > run-wideband.log 2>&1
```

Same short exchange.

**Pass:** `PREAMBLE: rate=16000`, normal speed, and audibly better than case 1.
The point of this pair is that **the same bot handled both with no code
change** — the rate came off the wire.

## 3. Barge-in

Keep the wideband run going, or start it again.

Ask something that produces a long answer — *"tell me everything you offer"* —
and **talk over the bot while it is mid-sentence**. Then ask a normal question
and let it answer.

**Pass:** the bot's audio stops promptly when you speak, and the next turn plays
normally. The log shows `INTERRUPTION: killAudio goes out on the socket`.

This is the case the removed guard affected. There used to be a 100 ms hold on
outgoing audio after `killAudio`; it is gone, so the next turn should start
without that delay. If audio from the interrupted sentence continues after you
speak, that is the finding, and it should go in the PR as one.

## 4. DTMF

Same run. The verb already sets `passDtmf: true`.

Press **1**, then **5**, then **\***. Wait a beat between them.

**Pass:** one `DTMF: caller pressed 1` line per press, with the right digit, and
**the call keeps working afterwards** — the bot still answers a question.

That last clause is the whole point. Before this fix a single digit went through
the preamble handler, nulled `callSid` and `traceId`, and left the rate
unusable, after which every audio frame was dropped and the call went silent.
So ask the bot something after pressing digits, and say in the PR that it
answered.

## 5. The bot ends the call

Same run. Press **#**.

**Pass:** the call clears from the softphone's side without you hanging up, and
the log shows `DTMF # means hang up: queueing EndFrame` then
`websocket closed; call over`.

`#` is wired to `EndFrame` only so the behaviour fires on demand rather than
when a language model decides — that way Dave can recreate it. The serializer
turns `EndFrame` into `{"type":"disconnect"}`, which ends the `listen` verb,
after which jambonz runs the verb's `actionHook` if it has one and otherwise
hangs up. If the verb had an `actionHook`, expect that to run instead of a
hangup.

---

## Posting it

On the PR, the verb JSON for each rate and one or two lines per case. Something
of this shape, with the real results:

```
Tested end to end against jambonz Cloud, Pipecat 1.12.0, softphone over SIP.
Verb used (SAMPLE_RATE set to 8000 for the narrowband run):

  { ...the verb JSON... }

1. G.711 / PCMU, 8 kHz   - preamble rate=8000 mono, bot intelligible, normal speed
2. Wideband, 16 kHz      - preamble rate=16000 mono, same bot, no code change
3. Barge-in mid-sentence - audio stopped on speech, next turn played normally
4. DTMF, passDtmf: true  - 1, 5 and * each arrived as InputDTMFFrame; call kept
                           working afterwards, which is the regression this fixes
5. EndFrame              - disconnect sent, listen verb ended, call cleared
```

Report what happened, including anything that did not work. A case that fails
is worth more in the PR than a case quietly left out, and it is the thing the
reviewer cannot get anywhere else.
