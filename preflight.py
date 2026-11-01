r"""Check everything a live call depends on, before dialling.

Written after an afternoon spent debugging a softphone that was never the
problem. The account's free trial had expired four days earlier, so jambonz
answered every REGISTER with 403 and every INVITE with 503. Nothing in the
portal said so; the account record did, in one field.

Each check below is something that was silently wrong at some point today.
They run in dependency order and the first failure usually explains the rest.

    .venv\Scripts\python.exe preflight.py

Needs JAMBONZ_ACCOUNT_SID and JAMBONZ_API_KEY in .env alongside the speech keys.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import struct
import urllib.error
import urllib.request

from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))

SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", "8000"))
JAMBONZ = "https://api.jambonz.cloud/v1"

failures: list[str] = []
state: dict[str, object] = {}


def check(name):
    def wrap(fn):
        try:
            print(f"  OK    {name:<26} {fn()}")
        except Exception as e:
            detail = e.args[0] if e.args else type(e).__name__
            print(f"  FAIL  {name:<26} {detail}")
            failures.append(name)
        return fn

    return wrap


def api(path: str):
    key = os.environ["JAMBONZ_API_KEY"]
    req = urllib.request.Request(JAMBONZ + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def post(url, headers, payload, raw=False):
    body = payload if raw else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


print("preflight: everything a live call depends on")
print()
print("-- the tunnel and the bot " + "-" * 44)


@check("ngrok tunnel")
def _ngrok():
    with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=10) as r:
        tunnels = json.load(r)["tunnels"]
    https = [t for t in tunnels if t["public_url"].startswith("https://")]
    if not https:
        raise AssertionError("no https tunnel; is `ngrok http 8080` running?")
    state["public_url"] = https[0]["public_url"]
    return f"{https[0]['public_url']} -> {https[0]['config']['addr']}"


@check("bot answers publicly")
def _bot():
    url = state.get("public_url")
    if not url:
        raise AssertionError("skipped: no tunnel")
    raw = post(
        url + "/",
        {"Content-Type": "application/json", "User-Agent": "preflight"},
        {"call_sid": "preflight", "from": "preflight", "to": "1234", "direction": "inbound"},
    )
    verb = json.loads(raw)[0]
    fork = verb["sampleRate"]
    back = verb["bidirectionalAudio"]["sampleRate"]
    if fork != back:
        raise AssertionError(f"rates disagree: forks at {fork}, reads back at {back} -- playback will run at {fork/back:.2f}x")
    if fork != SAMPLE_RATE:
        raise AssertionError(f"verb says {fork} Hz but SAMPLE_RATE is {SAMPLE_RATE}; restart the bot")
    if not verb["bidirectionalAudio"]["streaming"]:
        raise AssertionError("streaming is false -- jambonz will discard everything the bot says")
    return f"{fork} Hz both ways, mixType={verb['mixType']}, passDtmf={verb['passDtmf']}"


print()
print("-- the jambonz account " + "-" * 47)


@check("account is active")
def _active():
    acct = api(f"/Accounts/{os.environ['JAMBONZ_ACCOUNT_SID']}")
    state["account"] = acct
    end = acct.get("trial_end_date")
    if not acct.get("is_active"):
        why = acct.get("deactivated_reason") or (f"free trial ended {end[:10]}" if end else "no reason given")
        raise AssertionError(f"is_active=0 -- {why}. Every REGISTER gets 403 and every call gets 503.")
    if acct.get("plan_type") == "free" and end:
        left = (dt.datetime.fromisoformat(end.replace("Z", "+00:00")) - dt.datetime.now(dt.timezone.utc)).days
        return f"free trial, {left} day(s) left (ends {end[:10]})"
    return f"plan={acct.get('plan_type')}"


@check("device calling app")
def _devapp():
    acct = state.get("account")
    if not acct:
        raise AssertionError("skipped: account unreadable")
    sid = acct.get("device_calling_application_sid")
    if not sid:
        raise AssertionError("unset -- a call from the softphone has nowhere to go")
    app = api(f"/Applications/{sid}")
    hook = app.get("call_hook") or {}
    url = hook.get("url", "")
    state["app_name"] = app.get("name")
    public = str(state.get("public_url", ""))
    if public and public.rstrip("/") not in url:
        raise AssertionError(f"{app.get('name')!r} points at {url} but the tunnel is {public} -- update the calling webhook")
    if (hook.get("method") or "").lower() != "post":
        raise AssertionError(f"calling webhook method is {hook.get('method')!r}, should be POST")
    return f"{app.get('name')!r} -> {url} (POST)"


@check("softphone registered")
def _reg():
    users = api(f"/Accounts/{os.environ['JAMBONZ_ACCOUNT_SID']}/RegisteredSipUsers")
    if not users:
        raise AssertionError("none -- the softphone is not registered, so nothing can be dialled to it")
    # the endpoint returns bare strings, not objects
    return ", ".join(u.get("name", str(u)) if isinstance(u, dict) else str(u) for u in users)


print()
print("-- the speech and llm services " + "-" * 39)


@check("OpenRouter LLM")
def _llm():
    model = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    raw = post(
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"},
        {"model": model, "max_tokens": 4, "messages": [{"role": "user", "content": "say ok"}]},
    )
    return f"{model} replied {json.loads(raw)['choices'][0]['message']['content'].strip()!r}"


@check("Deepgram TTS")
def _tts():
    raw = post(
        f"https://api.deepgram.com/v1/speak?model=aura-2-thalia-en&encoding=linear16&sample_rate={SAMPLE_RATE}",
        {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "application/json"},
        {"text": "Pre-flight check."},
    )
    return f"{len(raw)} bytes of linear16 at {SAMPLE_RATE} Hz"


@check("Deepgram STT")
def _stt():
    pcm = struct.pack("<6400h", *([0] * 6400))
    raw = post(
        "https://api.deepgram.com/v1/listen?model=nova-3&encoding=linear16&sample_rate=16000",
        {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}", "Content-Type": "audio/raw"},
        pcm,
        raw=True,
    )
    return f"nova-3 accepted {json.loads(raw)['metadata']['duration']}s of raw PCM"


print()
if failures:
    print(f"NOT READY -- {len(failures)} failed: {', '.join(failures)}")
    print("The first failure usually explains the ones under it. Fix from the top down.")
    raise SystemExit(1)
print("READY -- dial 1234 from the softphone, or run place_call.py to be rung instead.")
