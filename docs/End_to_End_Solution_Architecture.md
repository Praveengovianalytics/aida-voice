# AIDA — End-to-End Solution Architecture

> **Version:** 0.7.4 | **Date:** February 2026 | **Author:** Solution Architecture
> **Platform:** Azure Container Apps + Azure VM | **Runtime:** Python 3.12 (async) + Node.js 20 (Puppeteer)

---

## Executive Summary

**AIDA** (AI Digital Assistant) is an **Employee Digital Twin** — an AI proxy that acts on behalf of an enterprise employee across text, voice, and live meeting modalities within Microsoft Teams. The platform combines retrieval-augmented generation (RAG), real-time voice conversation via Azure OpenAI Realtime API, live meeting intelligence with speaker-attributed transcription, and autonomous actions (email, calendar, presentations) orchestrated through Microsoft Graph. AIDA is deployed as a containerized Python service on Azure Container Apps with a companion Node.js screen-sharing microservice on an Azure VM.

---

## 1. Master End-to-End Architecture

The diagram below captures the complete system at a glance — every layer, service, and major data flow.

```mermaid
graph TB
    subgraph Users["👤 User Layer"]
        TeamsChat["Teams Chat<br/>(Text + Adaptive Cards)"]
        TeamsVoice["Teams Voice<br/>(WebSocket Client)"]
        TeamsMeeting["Teams Meeting<br/>(Multi-party Call)"]
    end

    subgraph Ingress["🔌 Ingress & Protocol Layer"]
        BotService["Azure Bot Service<br/>(Bot Framework)"]
        ACS["Azure Communication<br/>Services (ACS)<br/>Call Automation v1.5"]
        GraphWebhook["Graph Webhooks<br/>(Calling + Subscriptions)"]
    end

    subgraph AppLayer["⚙️ Application Layer"]
        direction TB
        subgraph PythonBot["Python Bot Service (aiohttp :3978)"]
            AidaBot["AidaBot<br/>Activity Handler"]
            VoiceGateway["Voice Gateway<br/>/voice-v2 (WSS)"]
            ACSWebhook["ACS Webhook<br/>/api/acs/events"]
            MeetingAudio["Meeting Audio<br/>Worker"]
            CallingWebhook["Calling Webhook<br/>/api/calling"]
        end
        subgraph ScreenShare["Screen Share Service (Node.js :8080)"]
            Puppeteer["Puppeteer<br/>(Headless Chrome)"]
            CTEAuth["CTE SDK<br/>(Teams Auth)"]
            AudioBridge["Audio Bridge<br/>(ScriptProcessorNode)"]
        end
    end

    subgraph Orchestration["🧠 Orchestration Layer"]
        RAG["RAG Pipeline"]
        ActionOrch["Action Orchestrator<br/>(Slot-filling FSM)"]
        MeetingIntel["Meeting Intelligence<br/>& Post-Processor"]
        DocIntel["Document Intelligence<br/>& Presenter"]
        PeopleRes["People Resolver<br/>(5-strategy cascade)"]
        EmailClass["Email Classifier"]
        ConvoMgr["Conversation Manager<br/>(Unified Context)"]
    end

    subgraph AI["🤖 AI Layer"]
        GPT4o["Azure OpenAI<br/>GPT-4o"]
        Realtime["Azure OpenAI<br/>Realtime API<br/>(gpt-4o-realtime)"]
        Embeddings["text-embedding-<br/>3-small (1536D)"]
        AISearch["Azure AI Search<br/>(Hybrid Vector+BM25)"]
    end

    subgraph Data["💾 Data Layer"]
        Redis["Azure Redis Cache<br/>(Sessions, Transcripts,<br/>Action State)"]
        CosmosDB["Azure Cosmos DB<br/>(Audit, Contacts,<br/>Meeting Notes)"]
        BlobStorage["Azure Blob Storage<br/>(Generated PPTs)"]
        SharePoint["SharePoint Online<br/>(Docs, Presentations,<br/>Meeting Notes)"]
    end

    subgraph Security["🔒 Security Layer"]
        EntraID["Microsoft Entra ID<br/>(2 App Registrations)"]
        KeyVault["Azure Key Vault<br/>(20+ Secrets)"]
        ManagedID["Managed Identity"]
    end

    subgraph Infra["☁️ Infrastructure"]
        ContainerApps["Azure Container Apps"]
        ACR["Azure Container<br/>Registry"]
        VM["Azure VM<br/>(Screen Share)"]
        AppInsights["Application Insights"]
    end

    %% User → Ingress
    TeamsChat -->|"Bot Framework"| BotService
    TeamsVoice -->|"WebSocket"| VoiceGateway
    TeamsMeeting -->|"ACS Events +<br/>Audio WSS"| ACS

    %% Ingress → App
    BotService -->|"POST /api/messages"| AidaBot
    ACS -->|"Events"| ACSWebhook
    ACS -->|"Audio WSS<br/>PCM16 24kHz"| MeetingAudio
    GraphWebhook -->|"Incoming call<br/>notification"| CallingWebhook

    %% App → Orchestration
    AidaBot -->|"Text"| ConvoMgr
    AidaBot -->|"Intent check"| ActionOrch
    AidaBot -->|"Free text"| RAG
    VoiceGateway -->|"Audio proxy"| Realtime
    MeetingAudio -->|"Transcript"| MeetingIntel
    MeetingAudio -->|"Audio bridge"| Realtime
    AidaBot -->|"Doc render"| DocIntel
    ActionOrch -->|"Name lookup"| PeopleRes

    %% Orchestration → AI
    RAG -->|"Embed query"| Embeddings
    RAG -->|"Hybrid search"| AISearch
    RAG -->|"Completion"| GPT4o
    ActionOrch -->|"Intent + slots"| GPT4o
    MeetingIntel -->|"Chunked summary"| GPT4o
    DocIntel -->|"Slide analysis"| GPT4o
    EmailClass -->|"Classification"| GPT4o
    PeopleRes -->|"Fuzzy match"| GPT4o

    %% Orchestration → Data
    ConvoMgr -->|"History"| Redis
    MeetingIntel -->|"Transcript"| Redis
    MeetingIntel -->|"Notes"| CosmosDB
    MeetingIntel -->|"Word .docx"| SharePoint
    ActionOrch -->|"State"| Redis
    PeopleRes -->|"Contacts"| CosmosDB

    %% Screen Share flows
    DocIntel -->|"REST API"| Puppeteer
    Puppeteer -->|"CTE join"| CTEAuth
    AudioBridge -->|"PCM16 WSS"| MeetingAudio

    %% Security
    KeyVault -->|"Secrets"| ManagedID
    ManagedID --> ContainerApps
    EntraID -->|"OAuth2"| BotService
    EntraID -->|"App tokens"| PeopleRes

    %% Infrastructure
    ACR -->|"Image pull"| ContainerApps
    ContainerApps -->|"Hosts"| PythonBot
    VM -->|"Hosts"| ScreenShare
    AppInsights -->|"Telemetry"| PythonBot

    %% Styling
    classDef userStyle fill:#4A90D9,stroke:#2C5F8A,color:#fff
    classDef ingressStyle fill:#E8A838,stroke:#B07A20,color:#fff
    classDef appStyle fill:#50B848,stroke:#357A30,color:#fff
    classDef orchStyle fill:#9B59B6,stroke:#6C3483,color:#fff
    classDef aiStyle fill:#E74C3C,stroke:#A93226,color:#fff
    classDef dataStyle fill:#1ABC9C,stroke:#148F77,color:#fff
    classDef secStyle fill:#F39C12,stroke:#B7770D,color:#fff

    class TeamsChat,TeamsVoice,TeamsMeeting userStyle
    class BotService,ACS,GraphWebhook ingressStyle
    class AidaBot,VoiceGateway,ACSWebhook,MeetingAudio,CallingWebhook,Puppeteer,CTEAuth,AudioBridge appStyle
    class RAG,ActionOrch,MeetingIntel,DocIntel,PeopleRes,EmailClass,ConvoMgr orchStyle
    class GPT4o,Realtime,Embeddings,AISearch aiStyle
    class Redis,CosmosDB,BlobStorage,SharePoint dataStyle
    class EntraID,KeyVault,ManagedID secStyle
```

---

## 2. Voice Engine & Real-Time Audio Pipeline

AIDA supports two voice interaction modes: **direct voice chat** (WebSocket from Teams client) and **meeting audio** (ACS media streaming in live Teams calls). Both converge on the Azure OpenAI Realtime API for transcription and response generation.

```mermaid
graph LR
    subgraph TeamsClient["Teams Client"]
        User["👤 User Speech"]
    end

    subgraph ACSLayer["ACS Media Stream"]
        ACSWSS["ACS WebSocket<br/>PCM16 24kHz base64"]
    end

    subgraph AudioWorker["Meeting Audio Worker"]
        ManualVAD["Manual VAD<br/>(energy threshold)"]
        EchoMute["Echo Cancellation<br/>(500ms post-speech)"]
        SilenceWD["Silence Watchdog<br/>(200ms independent poll)"]
        WakeWord["Wake-Word Gate<br/>'Hey AIDA'"]
        PendingQ["Pending Response<br/>Queue"]
    end

    subgraph RealtimeAPI["Azure OpenAI Realtime API"]
        STT["Server VAD +<br/>Transcription"]
        LLM["GPT-4o-Realtime<br/>+ Function Calling"]
        TTS["TTS Output<br/>(Sage voice)"]
    end

    subgraph BargeIn["Barge-In Handler"]
        StopAudio["StopAudio<br/>→ ACS WSS"]
        ResponseCancel["response.cancel<br/>→ Realtime API"]
        ItemTruncate["item.truncate<br/>→ Realtime API"]
    end

    subgraph CTEBridge["CTE Audio Bridge"]
        ScriptProc["ScriptProcessorNode<br/>PCM16 24kHz"]
        PuppeteerBrowser["Puppeteer<br/>(Headless Chrome)"]
    end

    subgraph Output["Audio Output"]
        ACSOut["ACS WebSocket<br/>(to meeting)"]
        VoiceOut["Voice WSS<br/>(to client)"]
    end

    User -->|"Audio frames"| ACSWSS
    ACSWSS --> EchoMute
    EchoMute --> ManualVAD
    ManualVAD -->|"Speech detected"| WakeWord
    WakeWord -->|"Activated"| STT
    ManualVAD -->|"Silence >300ms"| SilenceWD
    SilenceWD -->|"Commit audio"| STT

    STT --> LLM
    LLM -->|"Text response"| TTS
    LLM -->|"Tool calls"| PendingQ
    PendingQ -->|"Tool results"| LLM

    TTS -->|"Audio delta"| ACSOut
    TTS -->|"Audio delta"| VoiceOut

    User -->|"Interrupts"| BargeIn
    StopAudio --> ACSOut
    ResponseCancel --> LLM
    ItemTruncate --> LLM

    PuppeteerBrowser -->|"Meeting audio"| ScriptProc
    ScriptProc -->|"PCM16 WSS"| EchoMute
    ACSOut -->|"AIDA speech"| ScriptProc
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| Manual VAD over server VAD | Reduces false triggers in noisy meetings; independent silence watchdog (200ms) handles edge cases |
| Zero audio conversion | ACS and Realtime API share the same format (PCM16, 24kHz, mono) — no re-encoding overhead |
| Echo mute window (500ms) | Prevents AIDA from hearing its own TTS output as new speech input |
| Wake-word gating | Multi-party meetings use "Hey AIDA" activation; 1-on-1 calls are always-active |
| CTE audio bridge | Puppeteer ScriptProcessorNode captures meeting audio at 24kHz for the screen share service |

---

## 3. RAG & Knowledge Intelligence

The RAG pipeline provides grounded, citation-backed answers from the employee's personal knowledge base with strict data isolation.

```mermaid
graph TB
    subgraph Input["User Query"]
        Query["Natural language<br/>question"]
        History["Conversation<br/>history (Redis)"]
    end

    subgraph Embedding["Embedding"]
        EmbedModel["text-embedding-<br/>3-small<br/>(1536 dimensions)"]
    end

    subgraph Search["Azure AI Search"]
        VectorSearch["Vector Search<br/>(k-NN on<br/>content_vector)"]
        BM25["BM25 Keyword<br/>Search"]
        Filter["employee_id<br/>Filter<br/>(data isolation)"]
        HybridRank["Hybrid Ranking<br/>(RRF fusion)"]
    end

    subgraph Context["Context Assembly"]
        TopK["Top-5 documents<br/>with source metadata"]
        SystemPrompt["System prompt<br/>(role, company,<br/>employee context)"]
        ConvoHistory["Last 10 turns<br/>conversation<br/>history"]
    end

    subgraph Completion["GPT-4o Completion"]
        GPT["Azure OpenAI<br/>GPT-4o<br/>(temp=0.2)"]
    end

    subgraph Output["Response"]
        Answer["Grounded answer<br/>with inline citations"]
        Sources["Source attribution<br/>(title + relevance)"]
        Card["Adaptive Card<br/>→ Teams"]
    end

    Query --> EmbedModel
    EmbedModel -->|"1536D vector"| VectorSearch
    Query -->|"Raw text"| BM25
    Filter --> VectorSearch
    Filter --> BM25
    VectorSearch --> HybridRank
    BM25 --> HybridRank
    HybridRank --> TopK
    TopK --> GPT
    History --> ConvoHistory
    ConvoHistory --> GPT
    SystemPrompt --> GPT
    GPT --> Answer
    GPT --> Sources
    Answer --> Card
    Sources --> Card
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| Hybrid search (vector + BM25) | Captures both semantic similarity and exact keyword matches for higher recall |
| `employee_id` filter | Hard data isolation — each employee only sees their own indexed documents |
| Temperature 0.2 | Low creativity for factual, grounded answers; reduces hallucination |
| Top-5 retrieval | Balances context richness with prompt token efficiency |
| Unified conversation context | Chat and voice share the same history — user can switch modality seamlessly |

---

## 4. Meeting Intelligence & Post-Processing

AIDA joins Teams meetings via ACS, captures speaker-attributed transcripts, and produces professional meeting notes as Word documents uploaded to SharePoint.

```mermaid
graph TB
    subgraph LiveCall["Live Meeting"]
        ACSCall["ACS Call<br/>Automation"]
        AudioStream["Audio Stream<br/>(PCM16 24kHz)"]
        SpeakerID["Speaker Attribution<br/>(participantRawId)"]
    end

    subgraph Transcription["Live Transcript"]
        RealtimeSTT["Realtime API<br/>Transcription"]
        TranscriptMgr["LiveTranscript<br/>Manager"]
        PeriodicPersist["Periodic Persistence<br/>(every 5 entries)"]
    end

    subgraph Trigger["Post-Meeting Trigger"]
        CallDisconnect["CallDisconnected<br/>Event"]
        Delay["5-second delay<br/>(race condition fix)"]
    end

    subgraph Summarization["Chunked Summarization"]
        ChunkSplit["Split transcript<br/>into 20-min chunks"]
        ChunkSummary["Per-chunk GPT-4o<br/>summary"]
        Synthesis["Cross-chunk<br/>synthesis"]
        MeetingNotes["MeetingNotesData<br/>(structured output)"]
    end

    subgraph Deliverables["Deliverables"]
        WordDoc["Word .docx<br/>(python-docx)<br/>tables + styles"]
        SPUpload["SharePoint Upload<br/>(Graph API)"]
        AdaptiveCard["Adaptive Card<br/>'Open in SharePoint'"]
        CosmosStore["Cosmos DB<br/>(meeting-intelligence<br/>container)"]
    end

    ACSCall --> AudioStream
    AudioStream --> RealtimeSTT
    SpeakerID --> TranscriptMgr
    RealtimeSTT -->|"Text segments"| TranscriptMgr
    TranscriptMgr --> PeriodicPersist
    PeriodicPersist -->|"Redis"| Trigger

    CallDisconnect --> Delay
    Delay -->|"Load transcript<br/>from Redis"| ChunkSplit

    ChunkSplit -->|"< 30 min:<br/>single pass"| ChunkSummary
    ChunkSplit -->|">= 30 min:<br/>20-min chunks"| ChunkSummary
    ChunkSummary --> Synthesis
    Synthesis --> MeetingNotes

    MeetingNotes --> WordDoc
    MeetingNotes --> CosmosStore
    WordDoc --> SPUpload
    SPUpload --> AdaptiveCard
    AdaptiveCard -->|"Teams chat"| LiveCall
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| Chunked summarization (20-min chunks) | Prevents token limit issues for long meetings; preserves temporal context |
| Periodic transcript persistence | Fixes race condition where `CallDisconnected` fires before final transcript write |
| 5-second post-disconnect delay | Ensures all audio frames are processed before triggering summarization |
| Word .docx output | Professional formatting (tables, styles, colors) via `python-docx`; universally accessible |
| SharePoint over OneNote | Microsoft blocked app-only tokens for OneNote Graph endpoints (March 2025) |
| Speaker attribution | `participantRawId` from ACS audio frames mapped to display names via session roster |

---

## 5. Document Presentation & Screen Share

AIDA can find, render, and present documents via voice commands — searching SharePoint, converting to slide images, and screen-sharing into Teams meetings.

```mermaid
graph TB
    subgraph VoiceCmd["Voice Command"]
        UserCmd["'Show me the<br/>Q4 budget deck'"]
    end

    subgraph DocSearch["Document Search"]
        SPSearch["SharePoint<br/>Graph Search"]
        Download["Download file<br/>(PPTX/PDF/DOCX)"]
    end

    subgraph Rendering["Document Rendering"]
        LibreOffice["LibreOffice<br/>headless → PDF"]
        PDFRender["PDF → PNG pages<br/>(1920x1080)"]
        PreAnalysis["GPT-4o Vision<br/>pre-analysis<br/>(all slides)"]
    end

    subgraph ScreenShareSvc["Screen Share Service (Node.js VM)"]
        CTEJoin["CTE Auth →<br/>Join Teams Meeting"]
        PuppeteerCanvas["Puppeteer Canvas<br/>(slide display)"]
        GetDisplayMedia["getDisplayMedia<br/>intercept (1080p)"]
        TeamsShare["Teams Screen<br/>Share Stream"]
    end

    subgraph Navigation["Voice Navigation"]
        NavCmds["'next slide'<br/>'go to slide 3'<br/>'explain this'"]
        NavigateAPI["POST /present/<br/>navigate"]
        CanvasUpdate["Canvas re-render"]
        SessionUpdate["session.update<br/>(new slide context<br/>in instructions)"]
    end

    subgraph AudioBridgeLayer["Bidirectional Audio"]
        CaptureAudio["ScriptProcessorNode<br/>→ capture meeting audio"]
        InjectAudio["AudioContext<br/>→ inject AIDA speech"]
        WSBridge["WebSocket Bridge<br/>(PCM16 24kHz)"]
    end

    UserCmd --> SPSearch
    SPSearch -->|"File URL"| Download
    Download --> LibreOffice
    LibreOffice --> PDFRender
    PDFRender -->|"PNG base64 array"| PreAnalysis
    PreAnalysis -->|"Slide descriptions"| SessionUpdate

    PDFRender -->|"Slides payload"| PuppeteerCanvas
    CTEJoin --> PuppeteerCanvas
    PuppeteerCanvas --> GetDisplayMedia
    GetDisplayMedia --> TeamsShare

    NavCmds -->|"Voice tool call"| NavigateAPI
    NavigateAPI --> CanvasUpdate
    CanvasUpdate --> SessionUpdate

    CaptureAudio --> WSBridge
    WSBridge --> InjectAudio
    WSBridge -->|"Meeting audio<br/>→ Realtime API"| VoiceCmd
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| LibreOffice headless rendering | Server-side conversion of any Office format to PNG; no client-side dependency |
| GPT-4o Vision pre-analysis | All slides analyzed at load time, enabling instant voice explanations without per-slide API calls |
| CTE (Certified Teams Endpoint) | Only reliable way to programmatically join Teams meetings and share screen |
| `getDisplayMedia` intercept | Overrides Chromium's screen picker dialog for automated 1080p screen share |
| ScriptProcessorNode audio bridge | Bidirectional PCM16 24kHz bridge between Puppeteer browser and Python bot via WebSocket |

---

## 6. Action Orchestrator — Email, Calendar & People Resolution

AIDA detects user intent (send email, book meeting), extracts parameters via a slot-filling state machine, resolves people names, and executes through Microsoft Graph — all confirmable via Adaptive Cards.

```mermaid
graph TB
    subgraph Input["User Input"]
        ChatMsg["Chat message"]
        VoiceCmd["Voice command"]
    end

    subgraph IntentDetect["Intent Detection"]
        GPTClassify["GPT-4o<br/>Intent Classifier"]
        Confidence{"Confidence<br/>> 0.7?"}
    end

    subgraph SlotFilling["Slot-Filling FSM"]
        direction TB
        Collecting["COLLECTING<br/>(extract slots)"]
        SlotPrompt["Prompt for<br/>missing slots"]
        Previewing["PREVIEWING<br/>(show preview card)"]
        Confirmed["CONFIRMED"]
        Completed["COMPLETED"]
        Cancelled["CANCELLED"]
    end

    subgraph PeopleResolver["People Resolver (5-Strategy Cascade)"]
        S1["① Email regex<br/>(pass-through)"]
        S2["② Redis cache<br/>(recent resolutions)"]
        S3["③ Cosmos DB<br/>(known contacts)"]
        S4["④ Graph Directory<br/>(Users API)"]
        S5["⑤ Graph People API<br/>(relationship-ranked)"]
        S6["⑥ GenAI fuzzy match<br/>(GPT-4o disambiguation)"]
    end

    subgraph Execution["Graph API Execution"]
        SendMail["Graph: Send Mail"]
        CreateEvent["Graph: Create Event"]
    end

    subgraph Feedback["User Feedback"]
        PreviewCard["Preview<br/>Adaptive Card"]
        ConfirmBtn["Confirm / Edit /<br/>Cancel buttons"]
        SuccessCard["Success<br/>Adaptive Card"]
    end

    ChatMsg --> GPTClassify
    VoiceCmd --> GPTClassify
    GPTClassify --> Confidence
    Confidence -->|"Yes"| Collecting
    Confidence -->|"No → RAG"| Input

    Collecting -->|"Slots missing"| SlotPrompt
    SlotPrompt -->|"User provides"| Collecting
    Collecting -->|"All required<br/>slots filled"| Previewing

    Collecting -->|"Name → email"| S1
    S1 -->|"Miss"| S2
    S2 -->|"Miss"| S3
    S3 -->|"Miss"| S4
    S4 -->|"Miss"| S5
    S5 -->|"Ambiguous"| S6

    Previewing --> PreviewCard
    PreviewCard --> ConfirmBtn
    ConfirmBtn -->|"Confirm"| Confirmed
    ConfirmBtn -->|"Edit"| Collecting
    ConfirmBtn -->|"Cancel"| Cancelled

    Confirmed --> SendMail
    Confirmed --> CreateEvent
    SendMail --> Completed
    CreateEvent --> Completed
    Completed --> SuccessCard
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| Intent confidence threshold (0.7) | Below threshold falls through to RAG — prevents false action triggers |
| Slot-filling FSM in Redis | State persists across turns and modalities; user can start via voice, confirm via chat |
| 5-strategy people cascade | Progressively expensive lookups; instant cache hit for known contacts, GenAI fallback for ambiguity |
| Preview before execution | User always sees and confirms the action — prevents unintended emails or meetings |
| Cross-modal state | Action state keyed by `employee_id`, not conversation ID — works across chat and voice |

---

## 7. Azure Infrastructure & Security

AIDA's deployment topology spans Azure Container Apps, an Azure VM, and a suite of managed PaaS services, all secured through Entra ID and Key Vault.

```mermaid
graph TB
    subgraph Internet["Internet / Teams"]
        TeamsCloud["Microsoft Teams<br/>Cloud"]
        BotConnector["Azure Bot Service<br/>(Channel Connector)"]
    end

    subgraph ContainerEnv["Azure Container Apps Environment"]
        subgraph BotContainer["AIDA Bot Container"]
            Python["Python 3.12<br/>aiohttp :3978"]
            LibreOffice2["LibreOffice<br/>(headless)"]
        end
        Ingress["HTTPS Ingress<br/>(external)"]
    end

    subgraph VMEnv["Azure VM (Standard_D2s_v3)"]
        subgraph ScreenShareVM["Screen Share Service"]
            NodeJS["Node.js 20<br/>Express :8080"]
            Chrome["Chromium<br/>(Puppeteer)"]
            Xvfb["Xvfb<br/>(Virtual Display)"]
        end
    end

    subgraph Registry["Container Registry"]
        ACR2["Azure Container<br/>Registry"]
    end

    subgraph EntraIDSec["Microsoft Entra ID"]
        BotApp["Bot App Registration<br/>(c211596e-...)"]
        GraphApp["Graph App Registration<br/>(4ec1b314-...)"]
        CTEApp["CTE App Registration<br/>(Screen Share)"]
    end

    subgraph KeyVaultSec["Azure Key Vault"]
        Secrets["20+ secrets<br/>(API keys, connection<br/>strings, passwords)"]
    end

    subgraph ManagedIDSec["Managed Identity"]
        UAMI["User-Assigned<br/>Managed Identity"]
    end

    subgraph DataServices["Data & AI Services"]
        OpenAISvc["Azure OpenAI<br/>(East US 2)"]
        SearchSvc["Azure AI Search"]
        RedisSvc["Azure Cache<br/>for Redis"]
        CosmosSvc["Azure Cosmos DB<br/>(Serverless)"]
        BlobSvc["Azure Blob<br/>Storage"]
        SPSvc["SharePoint Online"]
        ACSsvc["Azure Communication<br/>Services"]
    end

    subgraph Observability["Observability"]
        AIns["Application Insights<br/>(OpenCensus)"]
    end

    %% Flows
    TeamsCloud -->|"Bot Framework"| BotConnector
    BotConnector -->|"HTTPS"| Ingress
    Ingress --> Python
    ACR2 -->|"Image pull"| BotContainer

    UAMI -->|"Token"| KeyVaultSec
    UAMI --> BotContainer
    Secrets -->|"Env injection"| Python

    BotApp -->|"Bot auth"| BotConnector
    GraphApp -->|"Client credentials"| SPSvc
    GraphApp -->|"Client credentials"| ACSsvc
    CTEApp -->|"ROPC flow"| Chrome

    Python -->|"REST API"| NodeJS
    Python --> OpenAISvc
    Python --> SearchSvc
    Python --> RedisSvc
    Python --> CosmosSvc
    Python --> BlobSvc
    Python --> SPSvc
    Python --> ACSsvc
    NodeJS --> ACSsvc

    Python -->|"Telemetry"| AIns
```

**Key Design Decisions**

| Decision | Rationale |
|----------|-----------|
| Container Apps (not AKS) | Serverless scaling, lower ops burden; sufficient for single-container workload |
| Separate VM for screen share | Puppeteer + Xvfb requires a persistent virtual display; Container Apps lacks GPU/display support |
| 2 app registrations | Bot identity (Teams channel) vs. Graph identity (mail, calendar, SharePoint) — separation of privilege |
| Key Vault + Managed Identity | No secrets in environment variables or code; auto-rotation capable |
| Cosmos DB Serverless | Cost-efficient for bursty workloads (meetings are episodic, not continuous) |
| `linux/amd64` Docker builds | Required for Container Apps; Mac Silicon defaults to ARM |

---

## Technology Stack Summary

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.12 (async) | Core bot service |
| **Language** | Node.js 20 | Screen share microservice |
| **Framework** | aiohttp + Bot Framework SDK | HTTP server + Teams integration |
| **Framework** | Express + Puppeteer | REST API + headless browser |
| **LLM** | Azure OpenAI GPT-4o | Chat completion, classification, summarization |
| **Voice** | Azure OpenAI Realtime API | Bidirectional voice (STT + LLM + TTS) |
| **Embeddings** | text-embedding-3-small | 1536D vectors for RAG |
| **Search** | Azure AI Search | Hybrid vector + BM25 retrieval |
| **Calling** | ACS Call Automation v1.5 | Teams interop, media streaming |
| **Graph** | Microsoft Graph SDK | Mail, Calendar, People, SharePoint, Calling |
| **Cache** | Azure Redis Cache | Sessions, transcripts, action state |
| **Database** | Azure Cosmos DB (Serverless) | Audit logs, contacts, meeting notes |
| **Storage** | Azure Blob Storage | Generated presentations |
| **Documents** | python-pptx, python-docx, LibreOffice | Document generation and rendering |
| **Identity** | Microsoft Entra ID | OAuth2, app registrations |
| **Secrets** | Azure Key Vault + Managed Identity | Secret management |
| **Hosting** | Azure Container Apps + Azure VM | Compute |
| **Observability** | Application Insights (OpenCensus) | Telemetry, metrics, tracing |

---

## Version History

| Version | Milestone |
|---------|-----------|
| v0.1 | RAG chat, meeting summaries, PowerPoint generation |
| v0.2 | Real-time voice via Azure OpenAI Realtime API |
| v0.3 | Email send, meeting booking, people resolver |
| v0.4 | ACS calling, meeting intelligence, wake-word detection |
| v0.5.6 | Voice call context, speaker tracking, web search |
| v0.5.9 | Chunked meeting notes, MeetingNotesData model |
| v0.6.0 | Word .docx to SharePoint (replaced OneNote) |
| v0.7.0 | Document intelligence, voice-controlled presentations |
| v0.7.3 | Screen share service, CTE audio bridge |
| v0.7.4 | Manual VAD, silence watchdog, manual barge-in |

---

*For detailed implementation reference, see [ARCHITECTURE.md](ARCHITECTURE.md).*
