"""Pretend to be jambonz against the real bot, so tomorrow's call is not the first run.

The unit tests exercise the serializer in isolation and the smoke test proved the
HTTP hook answers. Neither drives the websocket, which is where the transport,
the serializer and the pipeline actually meet -- so that path has never run.

This connects to /ws, sends the preamble jambonz sends, streams 20 ms frames at
the rate the preamble declared, and sends a DTMF text frame partway through.
Then it reports what the bot did with each.

It cannot test speech: synthetic tone is not words, so STT produces nothing and
the LLM never fires. That is the one part only a real call can cover.
"""

import asyncio
import json
import math
import struct
import sys

import websockets

RATE = 16000
FRAME_MS = 20
SAMPLES = RATE * FRAME_MS // 1000  # 320 samples -> 640 bytes, what jambonz sends

PREAMBLE = {
    "sampleRate": RATE,
    "mixType": "mono",
    "callSid": "3e5d869a-0000-0000-0000-000000000000",
    "direction": "inbound",
    "from": "smoke-test",
    "to": "1234",
    "callId": "94682bcc-0000-0000-0000-000000000000",
    "sipStatus": 200,
    "callStatus": "in-progress",
    "accountSid": "00000000-0000-0000-0000-000000000000",
    "traceId": "0" * 32,
}


def tone(index: int) -> bytes:
    """A 440 Hz frame. Not speech, but real audio at the right geometry."""
    start = index * SAMPLES
    return struct.pack(
        f"<{SAMPLES}h",
        *(int(8000 * math.sin(2 * math.pi * 440 * (start + n) / RATE)) for n in range(SAMPLES)),
    )


async def main(url: str, seconds: float = 3.0) -> int:
    received_binary = 0
    received_text: list[str] = []

    async with websockets.connect(url, max_size=None) as ws:
        print(f"connected to {url}")
        await ws.send(json.dumps(PREAMBLE))
        print("sent  preamble")

        async def listen():
            nonlocal received_binary
            try:
                async for msg in ws:
                    if isinstance(msg, bytes):
                        received_binary += len(msg)
                    else:
                        received_text.append(msg)
                        print(f"recv  TEXT {msg[:120]}")
            except websockets.ConnectionClosed:
                pass

        reader = asyncio.create_task(listen())

        frames = int(seconds * 1000 / FRAME_MS)
        for i in range(frames):
            await ws.send(tone(i))
            if i == frames // 2:
                await ws.send(json.dumps({"event": "dtmf", "dtmf": "1", "duration": "1600"}))
                print("sent  DTMF 1 (mid-stream, after the preamble)")
            await asyncio.sleep(FRAME_MS / 1000)
        print(f"sent  {frames} frames of {SAMPLES * 2} bytes ({seconds:.0f}s of audio)")

        await asyncio.sleep(1.5)
        reader.cancel()

    print()
    print(f"audio received back: {received_binary} bytes")
    print(f"text received back : {len(received_text)} frame(s)")
    return 0


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:8098/ws"
    sys.exit(asyncio.run(main(url)))
