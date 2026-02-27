# aida-voice -- AIDA Voice & Calling Gateway

The voice gateway for the AIDA platform.  Handles all voice interactions including Teams calling, ACS media streaming, and the bidirectional audio bridge to the OpenAI Realtime API.

**Port:** 3979

## Architecture

The voice service receives calls via Azure Communication Services (ACS) and Microsoft Teams, bridges live audio to the Azure OpenAI Realtime API for conversational AI, executes voice tools on behalf of the user, and streams response audio back to the caller.

```
                                 +-------------------+
                                 |   Microsoft Teams  |
                                 +--------+----------+
                                          |
                                          v
+------------------+    Incoming    +-----+--------+     Media Streaming
|  PSTN / VoIP     | ------------> |  ACS Call     | ----------------+
+------------------+    Call        |  Automation   |                 |
                                   +--------------+                  |
                                                                     v
                        +-----------+         +----------------------+---+
                        | Calling   | <----   | Voice Gateway (aiohttp)  |
                        | Webhook   |  POST   |                          |
                        +-----------+         |  /api/calls/incoming     |
                                              |  /api/calls/webhook      |
                        +-----------+         |  /api/calls/create       |
                        | ACS       | <----   |  /voice-v2  (WebSocket)  |
                        | Webhook   |  POST   |  /health                 |
                        +-----------+         +-------+------------------+
                                                      |
                                          +-----------+-----------+
                                          |                       |
                                          v                       v
                                +-------------------+   +-------------------+
                                | MeetingAudioWorker|   | Voice Tools       |
                                |                   |   |                   |
                                | ACS WS <-> RT WS  |   | get_call_context  |
                                +--------+----------+   | search_knowledge  |
                                         |              | get_meeting_notes |
                                         v              | get_calendar      |
                                +-------------------+   | send_email_draft  |
                                | OpenAI Realtime   |   | schedule_meeting  |
                                | API (gpt-4o)      |   | web_search        |
                                | WebSocket         |   | get_action_status |
                                +-------------------+   +-------------------+
```

### Voice Flow

```
Incoming Call
  -> ACS IncomingCall Webhook (/api/calls/incoming)
  -> Answer call with MediaStreaming config
  -> ACS connects WebSocket to /voice-v2
  -> VoiceGateway creates VoiceSession + MeetingAudioWorker
  -> Audio Worker bridges:
       ACS WebSocket (PCM audio) <-> OpenAI Realtime API WebSocket
  -> Realtime API processes speech, generates responses, calls tools
  -> Response audio streamed back through ACS WebSocket to caller
  -> On call end: persist transcript, trigger post-processing
```

## Dependencies

| Service | Purpose |
|---------|---------|
| **aida-sdk** | Shared clients (ACS, OpenAI, Graph, Search), configuration, contracts |
| **aida-data** | Transcript persistence, meeting notes storage, action item tracking |
| **aida-intelligence** | Post-call processing (summarisation, notes generation, SharePoint upload) |

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| WS | `/voice-v2` | WebSocket endpoint for ACS media streaming audio bridge |
| POST | `/api/calls/webhook` | ACS call lifecycle events (CallConnected, Disconnected, etc.) |
| POST | `/api/calls/incoming` | Teams/ACS incoming call notification -- answers with media config |
| POST | `/api/calls/create` | Create an outbound call (PSTN or VoIP) |
| GET | `/health` | Health check for container orchestrators |

## Voice Tools

| Tool | Description |
|------|-------------|
| `get_call_context` | Current call info -- participants, duration, meeting subject |
| `search_knowledge` | Search the org knowledge base (Azure AI Search) |
| `get_meeting_notes` | Retrieve past meeting notes from data service |
| `get_calendar` | Get calendar events for a time range (Microsoft Graph) |
| `send_email_draft` | Draft an email on behalf of the user (Microsoft Graph) |
| `schedule_meeting` | Schedule a calendar event with attendees (Microsoft Graph) |
| `web_search` | Search the web for current information |
| `get_action_status` | Check status of action items from past meetings |

## Wake Word Detection

In **meeting mode**, AIDA listens passively to the conversation and only activates when addressed directly:

- **Activation:** "Hey AIDA", "AIDA" (case-insensitive, includes common mis-transcriptions like "Ada")
- **Deactivation:** "Thanks AIDA", "That's all AIDA", "Never mind"
- Auto-deactivation after 30 seconds of inactivity (configurable)

In **direct call mode**, AIDA is always active -- no wake word needed.

## Meeting Mode vs Direct Call Mode

| Feature | Meeting Mode | Direct Call Mode |
|---------|-------------|-----------------|
| Voice activation | Wake word required | Always active |
| Transcript | Full meeting transcript captured | Conversation transcript captured |
| Post-processing | Meeting notes, action items, SharePoint upload | Conversation summary |
| Participants | Multiple (tracked via ACS events) | Typically one caller |
| Context | Meeting subject, attendee list | Caller identity |

## Speaker Tracking

The audio worker tracks speakers using the `participantRawId` field from ACS audio frames:

1. `ParticipantsUpdated` events populate the `speaker_map` (rawId -> display name).
2. Each audio frame includes the participant who generated it.
3. Transcript entries are attributed to the resolved speaker name.
4. The `get_call_context` tool exposes participant information to the Realtime API.

## Project Structure

```
aida-voice/
  voice_service/
    __init__.py              # Package init, version
    app.py                   # aiohttp application, routes, startup/shutdown
    voice_gateway.py         # WebSocket gateway, session management
    voice_state.py           # VoiceSession dataclass (per-call state)
    meeting_audio_worker.py  # Bidirectional audio bridge (ACS <-> Realtime API)
    voice_tools.py           # Tool definitions + dispatcher for Realtime API
    meeting_state.py         # Meeting session lifecycle management
    meeting_wake_word.py     # Wake word detection for meeting mode
    webhooks/
      __init__.py
      acs_webhook.py         # ACS call lifecycle event handler
      calling_webhook.py     # Incoming call handler (answers with media config)
  tests/
    __init__.py
  docs/
  .github/
    workflows/
      ci.yml                 # Lint + test + Docker build
      deploy.yml             # Build, push to ACR, deploy to Container Apps
  Dockerfile
  requirements.txt
  .gitignore
  README.md
```

## Local Development

### Prerequisites

- Python 3.11+
- An Azure Communication Services resource
- An Azure OpenAI resource with a Realtime API deployment
- Access to the `aida-sdk` package (install from the monorepo or local path)

### Setup

```bash
# Clone and navigate
cd aida-voice

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# For local aida-sdk development:
pip install -e ../aida-platform

# Copy environment template and fill in values
cp .env.example .env  # (create .env.example as needed)

# Run the service
python -c "from voice_service.app import main; main()"
```

### Running with Docker

```bash
# Build
docker build --platform linux/amd64 -t aida-voice .

# Run
docker run -p 3979:3979 --env-file .env aida-voice
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | HTTP server port | `3979` |
| `ACS_CONNECTION_STRING` | Azure Communication Services connection string | -- |
| `ACS_PHONE_NUMBER` | ACS phone number for outbound PSTN calls | -- |
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI resource endpoint | -- |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key | -- |
| `AZURE_OPENAI_REALTIME_DEPLOYMENT` | Realtime API deployment name | `gpt-realtime` |
| `BOT_CALLBACK_HOST` | Public callback URL for ACS webhooks | `https://localhost:8080` |
| `DATA_SERVICE_URL` | URL of the aida-data micro-service | `http://localhost:8081` |
| `INTELLIGENCE_SERVICE_URL` | URL of the aida-intelligence micro-service | `http://localhost:8082` |
| `GRAPH_TENANT_ID` | Azure AD tenant ID for Graph API | -- |
| `GRAPH_CLIENT_ID` | Graph app registration client ID | -- |
| `GRAPH_CLIENT_SECRET` | Graph app registration client secret | -- |
| `GRAPH_USER_EMAIL` | User email for delegated-style Graph calls | -- |
| `REDIS_URL` | Redis connection URL for session caching | `redis://localhost:6379` |
| `LANGFUSE_PUBLIC_KEY` | Langfuse public key for observability | -- |
| `LANGFUSE_SECRET_KEY` | Langfuse secret key for observability | -- |
| `APPINSIGHTS_CONNECTION_STRING` | Application Insights connection string | -- |

## Testing

```bash
# Install dev dependencies
pip install pytest pytest-asyncio ruff

# Lint
ruff check voice_service/ tests/

# Run tests
pytest tests/ -v
```
