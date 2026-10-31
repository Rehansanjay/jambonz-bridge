r"""Ring the softphone from jambonz instead of dialling into it.

Every inbound call from the registered client is being rejected by sbc-inbound
with a 503 before any application runs -- including jambonz's own hello-world
demo, so it is not this repo's code. An outbound call starts at the feature
server and dials out to the same client, which skips the stage that is failing.

If it connects, the `listen` verb runs exactly as it would on an inbound call,
so it is the same end-to-end test of the serializer, just dialled the other way.

    .venv\Scripts\python.exe place_call.py

Needs JAMBONZ_ACCOUNT_SID and JAMBONZ_API_KEY in .env. The docs give two
different base URLs and do not say whether a registered user is addressed by
bare username or user@realm, so each combination is tried until one is accepted
and the rest are reported.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

ACCOUNT_SID = os.environ["JAMBONZ_ACCOUNT_SID"]
API_KEY = os.environ["JAMBONZ_API_KEY"]
APPLICATION_SID = os.getenv("JAMBONZ_APPLICATION_SID", "547e860b-6989-479d-adc7-337b69d3e116")
SIP_USER = os.getenv("JAMBONZ_SIP_USER", "rehan")
SIP_REALM = os.getenv("JAMBONZ_SIP_REALM", "rehansanjay.sip.jambonz.cloud")

# docs.jambonz.org contradicts itself: the endpoint pages use the first, the
# introduction page tells jambonz.cloud users to use the second.
BASES = (
    "https://api.jambonz.cloud/v1",
    "https://jambonz.cloud/api",
)

# "name" is documented as "the name of a registered jambonz user" without
# saying whether the realm belongs in it.
NAMES = (SIP_USER, f"{SIP_USER}@{SIP_REALM}")


def attempt(base: str, name: str) -> tuple[bool, str]:
    body = json.dumps(
        {
            "from": "1234",
            "to": {"type": "user", "name": name},
            "application_sid": APPLICATION_SID,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/Accounts/{ACCOUNT_SID}/Calls",
        data=body,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, f"HTTP {r.status} {r.read().decode('utf-8', 'replace')[:200]}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def main() -> int:
    print("placing an outbound call to the registered softphone")
    print(f"  account     {ACCOUNT_SID}")
    print(f"  application {APPLICATION_SID}")
    print()
    for base in BASES:
        for name in NAMES:
            ok, detail = attempt(base, name)
            label = f"{base}  to.name={name!r}"
            print(f"  {'OK  ' if ok else 'fail'} {label}\n       {detail}")
            if ok:
                print()
                print("Call created. Your softphone should be ringing -- answer it.")
                return 0
    print()
    print("No combination was accepted. The output above is what to send Dave.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
