r"""Scaffolding, not the serializer: prove jambonz can reach a websocket and see
what actually arrives on it.

Two endpoints. POST / is the call hook -- jambonz asks it what to do and this
answers with a single `listen` verb pointing back at /ws. The websocket then
receives the forked call audio.

The point is to look at the traffic before writing anything that interprets it:

* how large each binary frame is, and whether the size is stable
* whether the byte total matches the sample rate we asked for, which is the
  cheapest way to catch a rate mismatch
* whether any TEXT frames arrive on the same socket, because that decides what
  the control path looks like and it is the open design question in
  SERIALIZER.md

Run it, expose it, point the application at it, call in and talk:

    ngrok http 8080
    set PUBLIC_HOST=<the ngrok host, no scheme>
    .venv\Scripts\python.exe -u probe_jambonz_socket.py

Then in the jambonz portal: a new application whose calling webhook is
https://<PUBLIC_HOST>/ , and Settings -> Device calling application pointing at
it. Dial anything from the softphone.

The -u only matters if you redirect this anywhere. Python block-buffers stdout
to a file or a pipe, so without it a run you wanted to keep writes nothing until
the process ends and looks, while you are watching it, like a run that failed.

The closing summary is printed when the socket closes, which is when the call
ends -- so hang up before reading the ratio. It is only meaningful for audio
that arrived in real time: feed this from anything faster and the ratio climbs
above 1.0 for reasons that have nothing to do with the sample rate.
"""

import json
import os
import time

from aiohttp import WSMsgType, web

PORT = int(os.getenv("PORT", "8080"))
PUBLIC_HOST = os.getenv("PUBLIC_HOST", "")  # ngrok host, no scheme
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "16000"))

# 16-bit signed mono, so two bytes per sample. Used only to turn a byte count
# back into seconds, which is what catches a rate that is not what we asked for.
BYTES_PER_SAMPLE = 2


async def call_hook(request: web.Request) -> web.Response:
    """What jambonz asks when a call arrives. Answer with one listen verb."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    print("\n--- call hook ---")
    for key in ("call_sid", "from", "to", "direction", "sip_status"):
        if key in payload:
            print(f"  {key}: {payload[key]}")
    print(f"  (payload had {len(payload)} keys)")

    ws_url = f"wss://{PUBLIC_HOST}/ws" if PUBLIC_HOST else f"ws://localhost:{PORT}/ws"
    verbs = [
        {
            "verb": "listen",
            "url": ws_url,
            "mixType": "mono",
            "sampleRate": SAMPLE_RATE,
            "bidirectionalAudio": {
                "enabled": True,
                "streaming": True,
                "sampleRate": SAMPLE_RATE,
            },
        }
    ]
    print(f"  -> listen {ws_url} at {SAMPLE_RATE} Hz")
    return web.json_response(verbs)


async def audio_socket(request: web.Request) -> web.WebSocketResponse:
    """Receive the forked audio and describe it rather than interpret it."""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    started = time.monotonic()
    frames = 0
    total_bytes = 0
    sizes: set[int] = set()
    text_frames: list[str] = []

    print("\n--- websocket open ---")
    async for msg in ws:
        if msg.type is WSMsgType.BINARY:
            frames += 1
            total_bytes += len(msg.data)
            sizes.add(len(msg.data))
            if frames <= 3 or frames % 100 == 0:
                print(f"  binary #{frames}: {len(msg.data)} bytes")
        elif msg.type is WSMsgType.TEXT:
            # This is the interesting one. If jambonz speaks JSON on the same
            # socket as the audio, the control path is in here.
            text_frames.append(msg.data[:400])
            print(f"  TEXT: {msg.data[:400]}")
        elif msg.type is WSMsgType.ERROR:
            print(f"  socket error: {ws.exception()}")

    wall = time.monotonic() - started
    audio_secs = total_bytes / (SAMPLE_RATE * BYTES_PER_SAMPLE) if total_bytes else 0.0

    print("\n--- websocket closed ---")
    print(f"  binary frames : {frames}")
    print(f"  total bytes   : {total_bytes}")
    print(f"  frame sizes   : {sorted(sizes) if len(sizes) <= 6 else f'{len(sizes)} distinct'}")
    print(f"  text frames   : {len(text_frames)}")
    print(f"  wall clock    : {wall:.2f}s")
    print(f"  audio implied : {audio_secs:.2f}s at {SAMPLE_RATE} Hz, 16-bit mono")
    if total_bytes and wall > 1:
        ratio = audio_secs / wall
        print(f"  ratio         : {ratio:.2f}  (1.0 means the rate is right;")
        print("                   0.5 means the real rate is half what we asked for,")
        print("                   2.0 means double)")
    return ws


def main() -> None:
    if not PUBLIC_HOST:
        print("PUBLIC_HOST is not set, so the verb will point at localhost and")
        print("jambonz will not be able to reach it. Set it to the ngrok host.")
    app = web.Application()
    app.router.add_post("/", call_hook)
    app.router.add_post("/call-hook", call_hook)
    app.router.add_get("/ws", audio_socket)
    print(f"listening on :{PORT}, expecting audio at {SAMPLE_RATE} Hz")
    web.run_app(app, port=PORT, print=None)


if __name__ == "__main__":
    main()
