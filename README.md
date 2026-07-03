# Huishouden-ochtendbriefing (cloud)

Stuurt Peter elke ochtend om **08:00 (Europe/Amsterdam)** een korte huishoud-briefing op
**WhatsApp**, volledig in de cloud via GitHub Actions — dus onafhankelijk van of de laptop aan staat.

## Hoe het werkt
- [`briefing.py`](briefing.py) berekent deterministisch welke taken vandaag spelen
  (weekdag-taken, Moos om-de-week, live ACV-afvaldata via Ximmio, verjaardagen, kwartaal-nudges)
  en verstuurt het bericht via de gratis [CallMeBot](https://www.callmebot.com/blog/free-api-whatsapp-messages/)-API.
- [`.github/workflows/ochtendbriefing.yml`](.github/workflows/ochtendbriefing.yml) draait het script
  dagelijks. GitHub cron draait in UTC, daarom staan er twee tijden (06:00 en 07:00 UTC); het script
  verstuurt alleen als het lokaal 08:xx is, dus exact 1× per dag — automatisch goed in zomer- én wintertijd.
- Een keepalive-stap voorkomt dat GitHub de cron na 60 dagen inactiviteit uitschakelt.

## Secrets (staan in de repo-instellingen, niet in de code)
- `CALLMEBOT_PHONE` — telefoonnummer met landcode, bv. `31646093574`
- `CALLMEBOT_APIKEY` — je CallMeBot-apikey
- `HUISHOUDEN_CONFIG` — JSON met persoonlijke gegevens (adres + verjaardagen), zodat die
  **niet in de broncode** staan en de repo veilig publiek kan zijn. Vorm:
  ```json
  {"postcode": "1234AB", "huisnr": "1",
   "verjaardagen": [["Naam", 5, 24, "traktatie"]]}
  ```
  Zonder deze secret werkt de briefing nog steeds, maar zonder afvaldata en verjaardagen.

Instellen: **Settings → Secrets and variables → Actions**, of via CLI:
```bash
gh secret set CALLMEBOT_PHONE
gh secret set CALLMEBOT_APIKEY
```

## Handmatig testen
- In GitHub: tab **Actions → Ochtendbriefing → Run workflow** (stuurt direct een bericht).
- Of via CLI: `gh workflow run ochtendbriefing.yml`
- Lokaal, zonder te versturen: `DRY_RUN=1 python briefing.py`

## Iets aanpassen
De regels staan bovenin `briefing.py` (weekdag-taken in `build_briefing`, Moos-referentiedatum,
verjaardagen, adres voor de afvaldata). De afvaldata worden live opgehaald, dus die hoef je nooit
handmatig te verversen.

> Losse variant op Peters laptop (`~/huishouden/`) blijft ook bestaan; die stuurt via een
> lokale scheduled task. Deze cloud-versie is het altijd-aan kanaal.
