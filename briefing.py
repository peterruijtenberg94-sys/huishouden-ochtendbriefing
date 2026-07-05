#!/usr/bin/env python3
"""
Huishouden-ochtendbriefing -> WhatsApp (via CallMeBot).

Draait deterministisch: berekent zelf welke taken vandaag spelen op basis van
de weekdag, Moos-parity, live ACV-afvaldata (Ximmio) en verjaardagen.
Bedoeld voor een dagelijkse cloud-cron (GitHub Actions), volledig los van Peters laptop.

Config via environment variables (GitHub Secrets):
  CALLMEBOT_PHONE   telefoonnummer met landcode, bv. 31646093574
  CALLMEBOT_APIKEY  CallMeBot apikey (cijfers)

Optioneel:
  FORCE_SEND=1  stuur ongeacht het uur (voor handmatige test-runs)
  DRY_RUN=1     bereken + print de briefing, maar verstuur niets
"""

import os
import sys
import json
import base64
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone


def _last_sunday(year: int, month: int) -> date:
    """Laatste zondag van (year, month)."""
    if month == 12:
        d = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def now_amsterdam() -> datetime:
    """Huidige wandkloktijd in Europe/Amsterdam, dependency-vrij (EU-DST-regels).
    Zomertijd (UTC+2) van laatste zondag maart 01:00 UTC t/m laatste zondag oktober 01:00 UTC,
    anders wintertijd (UTC+1). Retourneert een naïeve datetime in lokale tijd."""
    utc = datetime.now(timezone.utc)
    y = utc.year
    dst_start = datetime(y, 3, _last_sunday(y, 3).day, 1, tzinfo=timezone.utc)
    dst_end = datetime(y, 10, _last_sunday(y, 10).day, 1, tzinfo=timezone.utc)
    offset = 2 if dst_start <= utc < dst_end else 1
    return (utc + timedelta(hours=offset)).replace(tzinfo=None)

WEEKDAGEN = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]
MAANDEN = ["", "januari", "februari", "maart", "april", "mei", "juni",
           "juli", "augustus", "september", "oktober", "november", "december"]

# Moos: aanwezig in de week van ma 29-06-2026, daarna om-en-om (even weken = aanwezig).
MOOS_REF_MONDAY = date(2026, 6, 29)

# --- Persoonlijke gegevens komen uit env var HUISHOUDEN_CONFIG (GitHub Secret), ---
# --- zodat ze NIET in de (mogelijk publieke) broncode staan. Verwacht JSON:      ---
#   {"postcode": "1234AB", "huisnr": "1",
#    "verjaardagen": [["Naam", 5, 24, "traktatie"], ...]}
# Zonder config: geen afvaldata en geen verjaardagen (rest werkt gewoon).
def _load_config():
    # lstrip("﻿") verwijdert een eventueel UTF-8 BOM dat sommige tools
    # aan een secret-waarde plakken; anders faalt json.loads op teken 0.
    raw = os.environ.get("HUISHOUDEN_CONFIG", "").lstrip("﻿").strip()
    if not raw:
        return {"postcode": None, "huisnr": None, "verjaardagen": []}
    try:
        cfg = json.loads(raw)
        return {
            "postcode": cfg.get("postcode"),
            "huisnr": cfg.get("huisnr"),
            "verjaardagen": cfg.get("verjaardagen", []),
        }
    except Exception as e:
        print(f"[waarschuwing] HUISHOUDEN_CONFIG ongeldig: {e}", file=sys.stderr)
        return {"postcode": None, "huisnr": None, "verjaardagen": []}


CONFIG = _load_config()
VERJAARDAGEN = CONFIG["verjaardagen"]

# ACV / Ximmio (companyCode is de ACV-tenant, geen persoonsgegeven)
XIMMIO_COMPANY = "f8e2844a-095e-48f9-9f98-71fceb51d2c3"
XIMMIO_POSTCODE = CONFIG["postcode"]
XIMMIO_HUISNR = CONFIG["huisnr"]
FRACTIE_NL = {
    "GREY": "restafval (grijze bak)",
    "GREEN": "GFT (groene bak)",
    "PAPER": "oud papier",
    "PACKAGES": "PMD (plastic/blik/pak)",
    "TEXTILE": "textiel",
}


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weeks_between_mondays(d: date, ref: date) -> int:
    return (monday_of(d) - ref).days // 7


def moos_aanwezig(d: date) -> bool:
    return weeks_between_mondays(d, MOOS_REF_MONDAY) % 2 == 0


def fetch_afval(vandaag: date):
    """Haal ACV-ophaaldata live op. Retourneert dict {date: [fractie-namen]}.
    Faalt zacht: bij een fout een lege dict, zodat de briefing toch verstuurd wordt."""
    if not XIMMIO_POSTCODE or not XIMMIO_HUISNR:
        print("[info] geen adres in HUISHOUDEN_CONFIG - afvaldata overgeslagen.", file=sys.stderr)
        return {}
    try:
        def post(url, payload):
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, headers={"Content-Type": "application/json"}, method="POST"
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))

        addr = post("https://wasteapi.ximmio.com/api/FetchAdress", {
            "companyCode": XIMMIO_COMPANY,
            "postCode": XIMMIO_POSTCODE,
            "houseNumber": XIMMIO_HUISNR,
        })
        uid = addr["dataList"][0]["UniqueId"]

        cal = post("https://wasteapi.ximmio.com/api/GetCalendar", {
            "companyCode": XIMMIO_COMPANY,
            "uniqueAddressID": uid,
            "startDate": vandaag.strftime("%Y-%m-%d"),
            "endDate": (vandaag + timedelta(days=14)).strftime("%Y-%m-%d"),
        })

        out = {}
        for frac in cal.get("dataList", []):
            code = str(frac.get("_pickupTypeText", ""))
            naam = FRACTIE_NL.get(code, code)
            for ds in frac.get("pickupDates", []):
                # bv. "2026-07-16T00:00:00"
                d = datetime.fromisoformat(ds.split("T")[0]).date()
                out.setdefault(d, []).append(naam)
        return out
    except Exception as e:
        print(f"[waarschuwing] afvaldata ophalen mislukt: {e}", file=sys.stderr)
        return {}


def volgende_verjaardag_over(vandaag: date, maand: int, dag: int) -> int:
    """Aantal dagen tot de eerstvolgende verjaardag (0 = vandaag)."""
    jaar = vandaag.year
    try:
        d = date(jaar, maand, dag)
    except ValueError:  # 29 feb e.d.
        d = date(jaar, maand, 28)
    if d < vandaag:
        try:
            d = date(jaar + 1, maand, dag)
        except ValueError:
            d = date(jaar + 1, maand, 28)
    return (d - vandaag).days


def build_briefing(vandaag: date) -> str:
    wd = vandaag.weekday()  # ma=0 .. zo=6
    kop = f"☀️ Goedemorgen! Vandaag is het {WEEKDAGEN[wd]} {vandaag.day} {MAANDEN[vandaag.month]} {vandaag.year}."
    b = []

    # Elke dag
    b.append("\U0001F4F1 Even de bzZznder-app checken (insmeren / zwemkleding / gym?)")

    # Verjaardagen binnen 7 dagen
    for naam, mnd, dg, wat in VERJAARDAGEN:
        n = volgende_verjaardag_over(vandaag, mnd, dg)
        if n == 0:
            b.append(f"\U0001F382 Vandaag jarig: {naam} — {wat} regelen!")
        elif 1 <= n <= 7:
            dag_woord = "dag" if n == 1 else "dagen"
            b.append(f"\U0001F382 Over {n} {dag_woord} jarig: {naam} — {wat} regelen.")

    # Afval (vandaag / morgen)
    afval = fetch_afval(vandaag)
    morgen = vandaag + timedelta(days=1)
    for fr in afval.get(morgen, []):
        b.append(f"\U0001F5D1️ Vanavond {fr} buiten zetten (morgen ophaaldag).")
    for fr in afval.get(vandaag, []):
        b.append(f"\U0001F5D1️ Vandaag wordt {fr} opgehaald — staat de bak buiten?")

    # Per weekdag
    if wd == 1:  # dinsdag
        b.append("\U0001F956 Vers brood halen bij de bakker.")
    if wd == 0:  # maandag
        b.append("\U0001F6CF️ Beddengoed afhalen & wassen.")
    if wd in (0, 4, 5, 6):  # ma, vr, za, zo
        b.append("\U0001F373 Vanavond koken.")
    if wd in (0, 2, 4):  # ma, wo, vr
        b.append("\U0001FAB4 Planten water geven.")
        b.append("\U0001F9FA Was draaien.")
    if wd in (1, 5):  # di, za
        b.append("\U0001F454 Strijken.")
    if wd == 5:  # zaterdag
        b.append("\U0001F6D2 Picnic-bestelling plaatsen voor de levering van morgen (hele week, genoeg voor iedereen die mee-eet). Let op de bestel-deadline.")
    if wd == 6:  # zondag
        b.append("\U0001F6D2 Weekboodschappen via Picnic worden geleverd (hele week).")
        b.append("\U0001F9FD Opruimen & ordenen.")

    # Moos (om de week)
    if moos_aanwezig(vandaag):
        b.append("\U0001F415 Moos uitlaten (hij is deze week bij ons).")
        b.append("\U0001F9F9 Stofzuigen (hondenharen).")

    # Periodiek (zachte nudges)
    if wd == 0 and weeks_between_mondays(vandaag, MOOS_REF_MONDAY) % 6 == 0:
        b.append("✂️ Kapper voor de kinderen plannen?")
    if vandaag.day == 1 and vandaag.month in (1, 4, 7, 10):
        b.append("\U0001F455 Begin van het kwartaal — check of de kinderen niet uit kleren/schoenen zijn gegroeid.")

    tekst = kop + "\n\n" + "\n".join(f"- {item}" for item in b) + "\n\nFijne dag! \U0001F49B"
    return tekst


def verstuur(bericht: str, phone: str, apikey: str) -> bool:
    phone = "".join(ch for ch in phone if ch.isdigit())
    url = "https://api.callmebot.com/whatsapp.php?" + urllib.parse.urlencode({
        "phone": phone, "text": bericht, "apikey": apikey,
    })
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            low = body.lower()
            ok = ("queued" in low) or ("message sent" in low)
            bad = any(t in low for t in ("not valid", "not registered", "you need to", "invalid"))
            if bad and not ok:
                # Body niet loggen: die echoot telefoonnummer + berichttekst (PII) terug.
                print(f"[fout] CallMeBot weigerde het bericht (HTTP {resp.status}).", file=sys.stderr)
                return False
            print(f"[ok] WhatsApp verstuurd (HTTP {resp.status}).")
            return True
    except Exception as e:
        print(f"[fout] versturen mislukt: {e}", file=sys.stderr)
        return False


# --- Dagstatus in de repo, zodat er bij meerdere cron-runs per dag maar 1x wordt ---
# --- verstuurd (GitHub cron kan uren vertragen, dus draaien we vaker). De commit ---
# --- dient meteen als keepalive-activiteit voor de repo.                          ---
STATE_PATH = "data/last-sent.txt"


def _state_api(method, token, repo, data=None):
    url = f"https://api.github.com/repos/{repo}/contents/{STATE_PATH}"
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "huishouden-briefing",
    })
    return urllib.request.urlopen(req, timeout=20)


def reeds_verstuurd(vandaag, token, repo):
    """(al_verstuurd_vandaag: bool, sha: str|None). Zonder token/repo: (False, None)."""
    if not token or not repo:
        return False, None
    try:
        d = json.load(_state_api("GET", token, repo))
        inhoud = base64.b64decode(d.get("content", "")).decode().strip()
        return (inhoud == vandaag.isoformat()), d.get("sha")
    except Exception:
        return False, None  # bestaat nog niet -> nog niet verstuurd


def markeer_verstuurd(vandaag, token, repo, sha):
    if not token or not repo:
        return
    inhoud = base64.b64encode((vandaag.isoformat() + "\n").encode()).decode()
    body = {"message": f"briefing verstuurd {vandaag.isoformat()}", "content": inhoud}
    if sha:
        body["sha"] = sha
    try:
        _state_api("PUT", token, repo, json.dumps(body).encode())
        print(f"[info] gemarkeerd als verstuurd ({vandaag.isoformat()}).")
    except Exception as e:
        print(f"[waarschuwing] markeren mislukt (niet kritiek): {e}", file=sys.stderr)


def main() -> int:
    nu = now_amsterdam()
    vandaag = nu.date()

    force = os.environ.get("FORCE_SEND") == "1"
    dry = os.environ.get("DRY_RUN") == "1"
    token = os.environ.get("GH_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    sha = None

    if not force and not dry:
        # GitHub cron kan flink vertragen; daarom 'vanaf 08:00' i.p.v. 'precies 08:xx',
        # met een de-dup zodat er hooguit 1x per dag wordt verstuurd.
        if nu.hour < 8:
            print(f"[skip] lokaal uur is {nu.hour} (< 8) - nog te vroeg.")
            return 0
        al, sha = reeds_verstuurd(vandaag, token, repo)
        if al:
            print("[skip] briefing is vandaag al verstuurd.")
            return 0

    bericht = build_briefing(vandaag)

    if dry:
        # Alleen lokaal: volledige tekst tonen. In echte (publieke) runs NOOIT,
        # want Actions-logs van een publieke repo zijn voor iedereen zichtbaar.
        print("--- briefing (dry-run) ---")
        print(bericht)
        print("--- einde ---")
        print("[dry-run] niets verstuurd.")
        return 0

    aantal = bericht.count("\n- ")
    print(f"[info] briefing samengesteld voor {WEEKDAGEN[vandaag.weekday()]} {vandaag.isoformat()} ({aantal} taken).")

    phone = os.environ.get("CALLMEBOT_PHONE", "").strip()
    apikey = os.environ.get("CALLMEBOT_APIKEY", "").strip()
    if not phone or not apikey:
        print("[fout] CALLMEBOT_PHONE / CALLMEBOT_APIKEY ontbreken in de environment.", file=sys.stderr)
        return 1

    if not verstuur(bericht, phone, apikey):
        return 1

    # Alleen na succesvol versturen markeren (handmatige FORCE-run niet, die mag de
    # dagstatus niet beïnvloeden).
    if not force:
        markeer_verstuurd(vandaag, token, repo, sha)
    return 0


if __name__ == "__main__":
    sys.exit(main())
