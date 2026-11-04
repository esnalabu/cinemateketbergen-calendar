# Cinemateket Bergen kalender

Dette repoet genererer ei ICS-fil med filmprogrammet til
[Cinemateket Bergen](https://www.cinemateketbergen.no/program). Fila vert
oppdatert automatisk kvar måndag av ein GitHub Actions-jobb.

## Abonner

Kalenderen ligg på GitHub Pages:

```text
https://esnalabu.github.io/cinemateketbergen-calendar/cinemateketbergen.ics
```

GitHub Pages må aktiverast éin gong under **Settings → Pages** med
**Build and deployment → Source: GitHub Actions**. Workflowen publiserer berre
kalenderfila, ikkje resten av repoet. Actions Pages køyrer ikkje Jekyll, så
`.nojekyll` er ikkje naudsynt. GitHub Pages serverer `.ics` som `text/calendar`,
som er det kalenderappane forventar.

Den tidlegare reserve-URL-en til rå GitHub er fjerna; bruk Pages-URL-en.

Repoet eller Pages-sida må vere offentleg tilgjengeleg for at abonnementet skal
fungere utan autentisering.

## Kva som skjer

Workflowen `.github/workflows/update-calendar.yml` køyrer:

- kvar måndag kl. 06:00 UTC,
- manuelt via `workflow_dispatch`.

Jobben køyrer testane, hentar programmet, genererer
`_site/cinemateketbergen.ics`, validerer fila og publiserer ho til GitHub Pages.
Før generering prøver han å hente den førre publiserte kalenderen, slik at
`DTSTAMP` og `SEQUENCE` blir bevarte for hendingar som ikkje har endra seg. Viss
noko feilar, vert den førre publiserte fila ståande urørt. Workflowen krev minst
10 visingar, slik at ein delvis øydelagd parse ikkje overskriv kalenderen.

Skriptet:

- hentar `https://www.cinemateketbergen.no/program`,
- les `schema.org/ScreeningEvent`-markup,
- hentar start og slutt frå Ticketco-ICS for billettvisingar,
- validerer varigheit og fell tilbake til `Spilletid:` eller 120 minutt,
- rettar billettlause visingar med tydeleg tidsrange i skildringa,
- lagar heildagsevent når berre dato er truverdig,
- dedupliserer visingar som førekjem fleire gonger i HTML-en,
- tek med billettlause arrangement med fallback-UID,
- skriv UTC-tider til ICS.

## Tid og varigheit

For billettvisingar er Ticketco-ICS (`<ticket_url>.ics`) primærkjelde for start
og slutt. Han blir sanity-sjekka: dersom varigheita er openbert feil, til dømes
fleire dagar, brukar skriptet `Spilletid:` frå arrangementssida eller 120
minutt.

For billettlause visingar prøver skriptet å lese eit tydeleg tidsintervall frå
skildringa, til dømes «drop-in fra kl. 12 til 15». Viss ingen truverdig tid
finst, og visinga startar ved midnatt, blir ho lagt inn som heildagsevent på
datoen frå kjelda.

Prioritet:

1. Ticketco-ICS for billettvisingar
2. tidsrange i skildringa for billettlause visingar
3. strukturert starttid frå programmet
4. heildagsevent når berre dato er truverdig
5. hopp over visinga dersom ho ikkje kan plasserast

Alle korrigeringar og fallbackar blir skrivne som åtvaringar til `stderr`, slik
at dei er synlege i GitHub Actions-loggen.

## Køyr lokalt

Prosjektet brukar [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest -q
uv run python scripts/update_calendar.py
```

Skriptet skriv til `cinemateketbergen.ics` i repo-rota som standard. Bruk
`--output` for annan plass, `--no-detail` for å hoppe over detaljsidene, og
`--min-events` for å krevje eit minste tal visingar.

## Testar

Testane ligg i `tests/` og køyrer utan nettverk mot
`tests/fixtures/program_sample.html`, eit syntetisk utdrag av
programmarkupen. Dei dekkjer mellom anna duplikat, billettlause visingar,
Ticketco-ICS, varigheitsvalidering, tidskorrigering, heildagsevent,
spilletidsparsing, UTC-tider, UID-stabilitet, escaping og linjefolding.

## Avgrensingar

- `/program` inneheld i praksis inneverande og neste månad. Kalenderen blir
  derfor ikkje fullstendig for månader lenger fram.
- Nettsida kan endre markup, noko som kan bryte parsaren. Workflowen feilar då
  synleg, utan å overskrive den eksisterande ICS-fila.
- Ticketco-ICS kan i sjeldne tilfelle ha feil sluttid. Slike avvik blir
  sanity-sjekka og logga.
- Tidsrange i fritekst er ein heuristikk. Viss teksten er tvitydig, kan
  skriptet velje feil tid eller falle tilbake til heildagsevent.
- GitHub Actions kan forseinke planlagde køyringar.
- Kalenderappar oppdaterer abonnement på eiga tid; Google Calendar kan bruke
  timar eller dagar.
