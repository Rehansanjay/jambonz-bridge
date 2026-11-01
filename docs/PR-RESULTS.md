Ran this end to end against jambonz Cloud. Pipecat 1.12.0, a Zoiper5 softphone
registered to the account's SIP realm, no carrier. Three calls, all answered.

**Verb, narrowband run** (`SAMPLE_RATE=8000`):

```json
{
  "verb": "listen",
  "url": "wss://<host>/ws",
  "sampleRate": 8000,
  "mixType": "mono",
  "passDtmf": true,
  "bidirectionalAudio": { "enabled": true, "streaming": true, "sampleRate": 8000 }
}
```

The wideband run is the same verb with both rates set to `16000`. Nothing else
changed between the two, and no code changed: the rate is read from the preamble.

## Results

**1. G.711, 8 kHz.** The softphone was configured to offer only G.711 mu-law and
a-law, so nothing else could be negotiated. 47s, answered, `caller hungup`.

```
PREAMBLE: rate=8000 mixType=mono callSid=338c3504-19ff-47f2-a790-0ff287a52b12
```

The `callSid` matches the one in the call hook, so `serializer.call_sid` is
usable for correlating logs. Audio was intelligible at normal speed. I measured
the return path rather than trusting my ears: for a 2.7s sentence the serializer
emitted 2.75s of audio at 8 kHz. A rate mismatch would have been unmissable here
-- 24 kHz read as 8 kHz returns in 0.87s.

**2. Wideband, 16 kHz.** Same bot, same code, 86s, answered.

```
PREAMBLE: rate=16000 mixType=mono callSid=76aa7f6f-778c-4f27-89f8-2508082bf823
```

**3. Barge-in.** Talked over the bot mid-sentence; audio stopped and the next
turn played normally. Eight interruptions across the wideband call:

```
INTERRUPTION: killAudio goes out on the socket
```

**4. DTMF with `passDtmf: true`.** Pressed 1, 5 and * a second apart:

```
DTMF: caller pressed 1
DTMF: caller pressed 5
DTMF: caller pressed *
```

The call then kept working for another 40 seconds -- three more interruptions
and two more answered questions. That is the part worth checking: before this
change a digit went through the preamble handler, nulled `callSid` and
`traceId`, and every audio frame after it was dropped.

**5. The bot ending the call.** Pressed `#`, which the test bot wires to an
`EndFrame`:

```
DTMF # means hang up: queueing EndFrame
call status: {'call_status': 'completed', 'sip_status': 200, 'duration': 85}
```

jambonz's own call record gives `termination_reason: called party hungup` -- the
bot ended it, not me.

## Four things that are not about the serializer

Reporting these because they shaped what I heard, and a reviewer running this
would hit them too.

**Narrowband costs STT accuracy.** Asking "what are your opening hours" on the
G.711 call, Deepgram returned "what are the other", "what are your opening
thoughts" and "what are your opening costs" -- the word never survived. The same
sentences came back verbatim on the wideband call, same microphone, minutes
apart. Sibilant energy sits above G.711's 3.4 kHz ceiling, so the final
consonant is not in the audio to recognise.

**Barge-in is transcription-triggered in this pipeline.** The interruption fires
from `TranscriptionUserTurnStartStrategy`, so it waits for words to come back
from STT rather than cutting on VAD. That is a Pipecat pipeline choice, not
anything the serializer does, but it is why barge-in feels slower than the
`killAudio` timing suggests.

**Playout audio was degraded; the inbound direction was not.** My bot ran in
India and the media server for this account is in Virginia, so each 20ms of the
bot's speech crossed the Atlantic twice on a real-time deadline. Inbound audio
was clean -- Deepgram received whole sentences -- and the application logged no
underruns or dropped frames, so I read this as the test topology rather than
anything reproducible in a normal deployment. I have not packet-captured it, so
that is a reading rather than a proof.

**One turn was truncated.** On the wideband call a question came through as
"what are they opening" -- the turn was declared over before I finished. Again
pipeline tuning, not the serializer.

## Note on the first attempt

My first narrowband call used the verb at 8 kHz but I had not pinned the
softphone's codec, so the SIP leg almost certainly negotiated Opus and jambonz
resampled. The verb geometry was right but the leg was not G.711. Call 1 above
is the re-run with the codec list restricted, which is what the issue asked for.
