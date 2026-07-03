#!/usr/bin/env python3
"""
Keepalive: schrijft ~maandelijks een heartbeat-commit via de GitHub Contents-API,
zodat GitHub de geplande workflow niet uitschakelt na 60 dagen 'inactiviteit'.

Bewust géén externe action (dit account kan die niet benaderen) en géén git-clone:
puur een API-call met de automatische GITHUB_TOKEN.

Env (door Actions gezet): GITHUB_REPOSITORY, GITHUB_EVENT_NAME, GH_TOKEN.
"""
import os
import json
import base64
import datetime
import urllib.request

repo = os.environ["GITHUB_REPOSITORY"]
token = os.environ["GH_TOKEN"]
event = os.environ.get("GITHUB_EVENT_NAME", "")

# Alleen op de 1e van de maand (of bij een handmatige run) committen — ruim binnen
# de 60-dagen-grens, zonder dagelijkse ruis in de historie.
if event != "workflow_dispatch" and datetime.datetime.now(datetime.timezone.utc).day != 1:
    print("[keepalive] geen heartbeat vandaag.")
    raise SystemExit(0)

path = "data/heartbeat.txt"
api = f"https://api.github.com/repos/{repo}/contents/{path}"
headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
    "User-Agent": "huishouden-keepalive",
}


def call(method, data=None):
    req = urllib.request.Request(api, data=data, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=30)


# Bestaande sha ophalen (nodig om te updaten; ontbreekt bij de allereerste keer).
sha = None
try:
    sha = json.load(call("GET")).get("sha")
except Exception:
    pass

now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
content = base64.b64encode(f"laatste heartbeat: {now}\n".encode()).decode()
body = {"message": "keepalive heartbeat", "content": content}
if sha:
    body["sha"] = sha

try:
    call("PUT", json.dumps(body).encode())
    print(f"[keepalive] heartbeat geschreven: {now}")
except Exception as e:
    # Zacht falen: de briefing zelf is al verstuurd; dit mag de run niet laten klappen.
    print(f"[keepalive] mislukt (niet kritiek): {e}")
