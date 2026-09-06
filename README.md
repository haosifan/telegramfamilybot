# Family Task Bot

Ein **persönlicher Telegram-Aufgabenassistent** für Dinge, die im Alltag zwischen Tür und Angel entstehen.

Telegram ist die Bedienoberfläche, Notion die Ablage. Der Bot selbst läuft als sehr kleiner Docker-Container auf Proxmox. **Sprachtranskription und Sprachverständnis laufen über die OpenAI API**, daher braucht der Host weder lokales LLM noch Whisper-Modell, GPU oder nennenswerten Arbeitsspeicher.

Der Bot nutzt Telegram **Long Polling**. Es sind keine eingehenden Ports, keine öffentliche Domain und kein Telegram-Webhook erforderlich. Tailscale kann ausschließlich zur Administration des Proxmox-Hosts dienen.

## Was der MVP kann

- Textnachrichten in natürlicher deutscher Sprache verstehen.
- Telegram-Sprachnachrichten über OpenAI transkribieren.
- Aufgaben in Notion anlegen.
- Bestehende Aufgaben natürlichsprachlich verändern.
- Aufgaben erledigen.
- Mehrere Aktionen in einer Nachricht ausführen.
- Morgendlichen Tagescheck senden.
- Abends offene Schleifen vorlegen.
- Als `Urgent` markierte Aufgaben zu wenigen festen Uhrzeiten erneut melden.
- Zugriff auf genau **eine Telegram User ID** beschränken.

Beispiele:

```text
Morgen Schule anrufen wegen der Bescheinigung.
```

```text
Setze Paket wegbringen auf hohe Priorität und erinnere mich morgen dringlich.
```

```text
Paket morgen dringend, Rechnung Freitag und Schule ist erledigt.
```

```text
Was ist heute offen?
```

## Architektur

```text
Telegram App
    │
    │ Long Polling / Voice Download
    ▼
family-task-bot (kleiner Python-Container auf Proxmox)
    │
    ├── Text ───────────────────────► OpenAI Responses API
    │                                  GPT-5.6 Luna
    │                                  Structured Output
    │
    ├── Voice ──────────────────────► OpenAI Audio API
    │                                  GPT-4o mini Transcribe
    │                                      │
    │                                      ▼
    │                              OpenAI Responses API
    │
    ├── deterministische TaskService-Aktionen
    │
    ├── Morning / Evening / Urgent Scheduler
    │
    └───────────────────────────────► Notion Data Source
```

### Warum das ressourcenschonender ist

Auf dem Proxmox-Host laufen **keine Modelle**. Es werden weder Ollama noch Whisper oder Modell-Caches benötigt. Der Bot ist im Wesentlichen ein Python-Prozess mit Netzwerkzugriff.

## Wichtig: ChatGPT-Abo vs. OpenAI API

Ein bestehendes ChatGPT-Plus-/Pro-Abo kann nicht direkt als Backend dieses Bots verwendet werden. Für den Server wird ein **OpenAI API Key** benötigt. ChatGPT und API werden separat abgerechnet.

Für diesen Bot ist standardmäßig bewusst ein günstiges Modell vorgesehen:

```env
OPENAI_MODEL=gpt-5.6-luna
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
```

Der Task-Parser verarbeitet nur sehr kurze Texte und liefert ein kleines JSON zurück. Ein großes Reasoning-Modell wäre hier unnötig.

## 1. Telegram-Bot anlegen

1. In Telegram mit `@BotFather` einen Bot erzeugen (`/newbot`).
2. Token in `.env` eintragen.
3. Deine Telegram User ID in `TELEGRAM_ALLOWED_USER_ID` eintragen.

Wenn du die ID noch nicht kennst, kannst du den Container einmal mit einer falschen ID starten und `/start` senden. Der Bot antwortet nicht autorisierten Nutzern mit deren User ID. Danach `.env` korrigieren und neu starten.

## 2. OpenAI API einrichten

Erzeuge in der OpenAI API Platform einen API-Key und hinterlege ihn ausschließlich in deiner lokalen `.env`:

```env
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
TRANSCRIPTION_LANGUAGE=de
```

Der Key gehört **nicht** ins Git-Repository.

Für die Textinterpretation verwendet der Bot die Responses API mit **Structured Outputs**. Das Modell darf keine beliebigen Aktionen ausführen, sondern liefert lediglich ein validiertes Aktionsobjekt zurück.

Der Request verwendet `store=false`.

## 3. Notion-Datenbank vorbereiten

Lege in Notion eine Datenbank/Data Source mit diesen Properties an:

| Property | Typ | Werte |
|---|---|---|
| `Name` | Title | – |
| `Status` | Select | `Inbox`, `Open`, `Done` |
| `Due` | Date | – |
| `Priority` | Select | `Low`, `Normal`, `High` |
| `Reminder` | Select | `None`, `Normal`, `Urgent` |
| `Area` | Select | z. B. `Haushalt`, `Kind`, `Verwaltung`, `Sonstiges` |
| `Notes` | Rich text | – |
| `Source` | Select | `Telegram` |

Dann:

1. Notion Internal Integration erstellen.
2. Datenbank für diese Integration freigeben.
3. Integration Token in `NOTION_TOKEN` eintragen.
4. **Data Source ID** in `NOTION_DATA_SOURCE_ID` eintragen.

## 4. Konfiguration

```bash
cp .env.example .env
nano .env
```

Beispiel:

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:...
TELEGRAM_ALLOWED_USER_ID=123456789

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5.6-luna
OPENAI_TRANSCRIPTION_MODEL=gpt-4o-mini-transcribe
TRANSCRIPTION_LANGUAGE=de

# Notion
NOTION_TOKEN=ntn_...
NOTION_DATA_SOURCE_ID=...

# Routinen
TIMEZONE=Europe/Berlin
MORNING_REVIEW_TIME=07:15
EVENING_REVIEW_TIME=20:30
URGENT_REMINDER_TIMES=09:00,15:00,19:00
```

`Urgent` bedeutet nicht Dauerbeschallung: Eine dringende, heute fällige Aufgabe wird nur zu diesen festen Zeitpunkten nochmals gemeldet.

## 5. Start auf Proxmox

Geeignet ist z. B. eine kleine Debian/Ubuntu-VM oder ein LXC mit Docker.

```bash
git clone <dein-repository> family-task-bot
cd family-task-bot
cp .env.example .env
nano .env
docker compose up -d --build
```

Logs:

```bash
docker compose logs -f familybot
```

Update:

```bash
git pull
docker compose up -d --build
```

Es gibt bewusst:

- keine veröffentlichten Docker-Ports,
- kein Ollama,
- kein Whisper-Modell auf dem Server,
- keinen HuggingFace-Cache,
- keine GPU-Konfiguration,
- kein n8n.

## Bedienlogik

### Neue Aufgabe

```text
Morgen Paket wegbringen.
```

→ `Due=morgen`, `Priority=Normal`, `Reminder=Normal`.

Ohne Termin:

```text
Versicherungsvertrag prüfen.
```

→ `Status=Inbox`, kein künstlich erfundenes Datum.

### Änderung

```text
Paket wegbringen auf hoch und morgen dringend erinnern.
```

Der Parser erzeugt nur eine strukturierte Aktion. Beispielhaft:

```json
{
  "action": "update",
  "task_query": "Paket wegbringen",
  "due": "2026-09-07",
  "priority": "High",
  "reminder": "Urgent"
}
```

Das eigentliche Finden und Ändern der Notion-Aufgabe übernimmt anschließend deterministischer Python-Code.

Bei einem nicht eindeutigen Aufgabennamen wird nicht geraten, sondern nachgefragt.

### Morgenroutine

Der Bot zeigt:

- heute fällige Aufgaben,
- überfällige Aufgaben,
- Anzahl der Inbox-Aufgaben.

Danach kannst du direkt per Text oder Sprache umplanen.

### Abendschleife

Der Bot zeigt die offenen bzw. überfälligen Aufgaben. Eine Antwort darf mehrere Aktionen enthalten:

```text
Paket morgen dringend, Rechnung Freitag, Schule erledigt.
```

## Verzeichnis

```text
family-task-bot/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── pyproject.toml
├── README.md
├── tests/
└── src/familybot/
    ├── config.py
    ├── llm.py
    ├── main.py
    ├── models.py
    ├── notion.py
    ├── reviews.py
    ├── service.py
    └── stt.py
```

## Datenschutz / Datenfluss

Bei einer **Textnachricht** gehen die zur Interpretation nötigen Eingaben an OpenAI. Bei einer **Sprachnachricht** wird die Telegram-Audiodatei zunächst an die OpenAI Audio-Transkriptions-API geschickt; anschließend wird der transkribierte Text durch den Task-Parser verarbeitet.

Notion erhält die daraus erzeugten bzw. aktualisierten Aufgabendaten.

Damit ist diese Variante wesentlich ressourcenschonender als eine lokale Modellpipeline, bedeutet aber bewusst, dass Aufgabeninhalte und Sprachaufnahmen externe Dienste durchlaufen.

## Bewusste MVP-Grenzen

Noch nicht enthalten:

- Inline-Buttons zum Abhaken/Verschieben.
- Kontextdialog über mehrere Telegram-Nachrichten hinweg (`"das zweite"`).
- Wiederkehrende Aufgaben.
- Wochenreview.
- Persistentes Audit-Log aller Bot-Aktionen.
- Weboberfläche.

Der erste Praxistest sollte weiterhin nur beantworten, ob **Capture + Morgenroutine + Abendschleife** die gewünschte Routine zuverlässig herstellen.
