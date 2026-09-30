#!/usr/bin/env python3
"""
NMG Wonen – woningalarm Nijmegen
================================
Checkt elke paar minuten https://nmgwonen.nl/huur/ (alle pagina's) en stuurt
een pushmelding naar je telefoon zodra er een NIEUWE woning in Nijmegen
verschijnt. Meldingen gaan via ntfy.sh (gratis, geen account nodig).

Gebruik:
    python nmg_woningalarm.py          -> blijft draaien en checkt continu
    python nmg_woningalarm.py --test   -> stuurt alleen een testmelding
    python nmg_woningalarm.py --once   -> één check en dan stoppen (voor taakplanner)

Alleen standaard Python nodig (3.8+), geen extra pakketten.
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ======================= INSTELLINGEN =======================
# Topic wordt uit een GitHub-secret gehaald (zodat niemand hem ziet).
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "verander-dit-naar-iets-unieks-8413")
STAD = "nijmegen"          # plaatsnaam zoals die in de woning-URL staat
INTERVAL_SEC = 180         # hoe vaak checken (seconden). 180 = elke 3 minuten
MAX_PRIJS = None           # bijv. 1300 om duurdere woningen te negeren, None = alles
# ============================================================

BASE = "https://nmgwonen.nl/huur/"
STATE_FILE = Path(__file__).with_name("nmg_gezien.json")
HEADERS = {"User-Agent": "Mozilla/5.0 (persoonlijk woningalarm)"}

LINK_RE = re.compile(
    r'href="(?:https?://nmgwonen\.nl)?/woning/([a-z0-9\-]+)/?"', re.IGNORECASE
)
PRIJS_RE = re.compile(r"€\s*([\d\.]+),-\s*p/m")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def pagina_url(n):
    return BASE if n == 1 else f"{BASE}page/{n}/"


def haal_alle_woningen():
    """Geeft dict {slug: prijs_of_None} van alle woningen op alle pagina's."""
    woningen = {}
    for n in range(1, 40):
        try:
            html = fetch(pagina_url(n))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                break  # voorbij de laatste pagina
            raise

        nieuw_op_pagina = 0
        for m in LINK_RE.finditer(html):
            slug = m.group(1).lower()
            if slug in woningen:
                continue
            # prijs staat in de HTML vlak vóór de link van de woningkaart
            venster = html[max(0, m.start() - 2500): m.start()]
            prijzen = PRIJS_RE.findall(venster)
            prijs = int(prijzen[-1].replace(".", "")) if prijzen else None
            woningen[slug] = prijs
            nieuw_op_pagina += 1

        if nieuw_op_pagina == 0:
            break  # pagina zonder nieuwe woningen = einde
        time.sleep(1)  # netjes blijven tegen de server
    return woningen


def mooie_naam(slug):
    rest = slug[len(STAD) + 1:] if slug.startswith(STAD + "-") else slug
    return rest.replace("-", " ").title()


def meld(titel, tekst, link=None, prio="high", tags="house"):
    req = urllib.request.Request(
        f"https://ntfy.sh/{NTFY_TOPIC}",
        data=tekst.encode("utf-8"),
        method="POST",
    )
    req.add_header("Title", titel)      # titel ASCII houden (HTTP-header)
    req.add_header("Priority", prio)
    req.add_header("Tags", tags)
    if link:
        req.add_header("Click", link)   # tik op melding -> woningpagina
    urllib.request.urlopen(req, timeout=15).close()


def laad_gezien():
    if STATE_FILE.exists():
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    return None


def sla_gezien_op(gezien):
    STATE_FILE.write_text(json.dumps(sorted(gezien), indent=1), encoding="utf-8")


def check(gezien):
    alles = haal_alle_woningen()
    if not alles:
        raise RuntimeError("0 woningen gevonden – site onbereikbaar of structuur veranderd")

    stad = {s: p for s, p in alles.items() if s.startswith(STAD + "-")}
    log(f"{len(alles)} woningen totaal, {len(stad)} in {STAD.title()}")

    if gezien is None:  # eerste run: huidige aanbod onthouden, niet spammen
        sla_gezien_op(set(stad))
        meld("Woningalarm actief",
             f"Ik let op. Nu {len(stad)} woningen in {STAD.title()} bekend. "
             f"Nieuwe krijg je direct binnen.", prio="default", tags="eyes")
        log("Eerste run: huidige woningen opgeslagen.")
        return set(stad)

    nieuw = [s for s in stad if s not in gezien]
    for slug in nieuw:
        prijs = stad[slug]
        if MAX_PRIJS and prijs and prijs > MAX_PRIJS:
            log(f"Overgeslagen (te duur, €{prijs}): {slug}")
            continue
        url = f"https://nmgwonen.nl/woning/{slug}/"
        prijs_txt = f"€ {prijs:,}".replace(",", ".") + " p/m" if prijs else "prijs onbekend"
        meld(f"Nieuwe woning in {STAD.title()}!",
             f"{mooie_naam(slug)}\n{prijs_txt}\n{url}", link=url, prio="urgent")
        log(f"NIEUW -> melding gestuurd: {slug} ({prijs_txt})")

    gezien |= set(nieuw)
    sla_gezien_op(gezien)
    return gezien


def main():
    if "--test" in sys.argv:
        meld("Testmelding woningalarm", "Werkt! Zo ziet een melding eruit.",
             link=BASE, prio="high")
        log("Testmelding verstuurd.")
        return

    gezien = laad_gezien()
    fouten = 0
    while True:
        try:
            gezien = check(gezien)
            fouten = 0
        except Exception as e:
            fouten += 1
            log(f"Fout ({fouten}x achter elkaar): {e}")
            if fouten == 10:
                try:
                    meld("Woningalarm heeft problemen",
                         f"Al 10 checks mislukt. Laatste fout: {e}",
                         prio="default", tags="warning")
                except Exception:
                    pass
        if "--once" in sys.argv:
            break
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    main()
