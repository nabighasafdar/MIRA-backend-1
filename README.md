# MIRA — Multi-step Internet Reasoning Agent

> **Accessible Web Automation for the Visually Impaired**

MIRA is an AI-powered web automation platform. It bridges the accessibility gap for blind and visually impaired individuals by letting an AI agent understand natural language voice commands and autonomously carry out web tasks without requiring any visual interaction from the user.


## 🎯 Problem & Motivation

Over **285 million visually impaired people** worldwide rely on assistive tools like screen readers. These tools are fragile — they break when website layouts change, force users to listen through irrelevant content, and cannot adapt to user intent. MIRA solves this by:

- Understanding user intent from **natural, conversational voice commands**
- **Autonomously executing** multi-step web tasks (navigation, clicks, form-filling)
- **Adapting** to website structure changes without breaking workflows
- Providing **content accessibility tools**: text summarization, image descriptions, and decluttered page views
- Allowing users to **save and re-execute** frequently used task workflows

---

## ✨ Features

### Core Agent
- **Vision + DOM Hybrid** — Combines live screenshots with a structured DOM tree for accurate, context-aware actions
- **Multi-LLM Support** — Powered by **Google Gemini** and **OpenAI** models
- **Multi-step Task Planning** — Interprets high-level intent and breaks it into sequential web actions
- **Multi-turn Follow-ups** — Maintains agent state across conversation turns so the agent remembers context

### Accessibility
- **Voice Command Interface** — Speech-to-Text (STT) input; Text-to-Speech (TTS) output
- **Content Summarization** — Condenses web pages into concise, spoken summaries
- **Image Description** — Generates audio descriptions for visual content on any page

### User Features
- **InfoWallet** — Securely stores user data (name, address, credentials) and automatically fills forms on the user's behalf
- **Workflow Macros (Task Bookmarking)** — Record agent action sequences as reusable workflows; save, re-execute, or delete them
- **Persistent Browser Profiles** — Agent can operate within a dedicated Chrome profile (preserving logins and cookies)
- **Issue Reporting** — Users can report problems directly to the Admin

### Admin & Billing
- **Admin Module** — View, add, delete users and reset passwords
- **Token-based Billing** — Tracks and calculates billing based on AI token consumption per session

---

## 🏗️ Backend Architecture

This repository is the **MIRA agent backend** — the Python engine that drives the autonomous browser agent.

```
MIRA/
├── system/                  # Core agent engine
│   ├── agent/               # Agent run loop, prompts, history, workflow
│   │   ├── service.py       # Main Agent class — orchestrates the execution loop
│   │   ├── prompts.py       # SystemPrompt & AgentMessagePrompt builders
│   │   ├── views.py         # Pydantic models: ActionResult, AgentHistoryList, etc.
│   │   ├── workflow.py      # WorkflowTemplate — record & replay action macros
│   │   ├── judge.py         # Self-evaluation / task success judge
│   │   ├── gif.py           # GIF generation from session screenshots
│   │   └── system_prompts/  # Markdown prompt templates (thinking / flash / no-thinking)
│   ├── browser/             # Browser session lifecycle & profile management
│   ├── dom/                 # DOM parsing & LLM-friendly tree representation
│   ├── llm/                 # LLM provider abstraction (Google Gemini, OpenAI)
│   ├── tools/               # Action registry (click, type, scroll, extract, navigate…)
│   ├── wallet/              # InfoWallet — per-user persistent key-value store
│   ├── tokens/              # Token counting & billing utilities
│   ├── telemetry/           # Usage telemetry
│   ├── config.py            # Pydantic-settings config (reads .env)
│   └── utils.py             # Shared helpers
├── tests/
│   ├── robustness/          # Scenario tests: static, dynamic, forms, iframes, overlays…
│   └── profile/             # Live-profile agent tests (persistent Chrome session)
├── wallets/                 # Runtime wallet files per user (auto-created)
├── mira_dedicated_profile/  # Permanent Chrome profile for authenticated tests
├── pyproject.toml
└── .env                     # API keys — never commit this file
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python **3.11+**
- Google Gemini **or** OpenAI API key
- Chromium (installed via Playwright)

### 2. Install Dependencies

```bash
pip install -e .
```

### 3. Install Playwright Browsers

```bash
playwright install chromium
```

### 4. Configure Environment

Create a `.env` file in the project root:

```env
GOOGLE_API_KEY=your_google_api_key_here
OPENAI_API_KEY=your_openai_api_key_here   # optional
```

---

## 🧪 Running Tests

### Robustness Tests

Validate the agent against a wide range of real-world web scenarios:

```bash
cd tests/robustness

python test_1a_static.py       # Static page navigation
python test_1b_dynamic.py      # JS-rendered SPA content
python test_1c_ecommerce.py    # E-commerce product & cart flows
python test_1d_iframe.py       # Iframe interaction
python test_2a_multitab.py     # Multi-tab browsing
python test_2b_form.py         # Form filling
python test_2c_math.py         # Computation & data extraction
python test_3a_blocker.py      # Cookie banners & pop-up blockers
python test_3b_overlays.py     # Modal & overlay dismissal
python test_3c_missing.py      # Graceful handling of missing information
python test_4a_followup.py     # Multi-turn interactive chat session
```

Or run the full suite from the project root:

```bash
pytest tests/robustness/
```

### Workflow Macro Test

Records a task as a reusable macro, then replays it:

```bash
python tests/robustness/test_1d_macro.py
```

### Live Profile Agent Test

Connects the agent to a persistent Chrome profile (e.g., pre-logged into Gmail):

```bash
# One-time setup: open a browser window and log in manually
python tests/profile/mira_profile_setup.py

# Run the agent using your saved live profile
python tests/profile/test_mira_agent.py
```

---

## 💡 Usage — Embedding the Agent

```python
import asyncio
from system.agent.service import Agent
from system.browser.session import BrowserSession
from system.browser.profile import BrowserProfile
from system.llm import ChatGoogle

async def main():
    llm = ChatGoogle(model="gemini-2.5-flash", api_key="YOUR_KEY")
    profile = BrowserProfile(headless=False)
    session = BrowserSession(browser_profile=profile)

    agent = Agent(
        task="Go to wikipedia.org and summarize the featured article.",
        llm=llm,
        browser_session=session,
    )

    await session.start()
    history = await agent.run()
    print(history.final_result())
    await session.stop()

asyncio.run(main())
```

### Recording & Replaying Workflow Macros

```python
from system.agent.workflow import WorkflowTemplate

# After an agent run, save the workflow:
workflow = WorkflowTemplate.from_history(history, original_task=task)
with open("my_workflow.json", "w") as f:
    f.write(workflow.model_dump_json(indent=2))

# Replay it later:
template = WorkflowTemplate.model_validate_json(open("my_workflow.json").read())
agent = Agent(task=template.original_task, llm=llm, browser_session=session, workflow_template=template)
```

---


## 📦 Key Dependencies

| Package | Purpose |
|---|---|
| `playwright` | Browser automation via Chromium CDP |
| `google-genai` | Google Gemini LLM provider |
| `openai` | OpenAI LLM provider |
| `pydantic` / `pydantic-settings` | Data validation & settings management |
| `aiohttp` / `httpx` | Async HTTP communication |
| `python-dotenv` | `.env` file loading |
| `bubus` / `cdp-use` | CDP utilities for low-level browser control |
| `psutil` | System process management |

---

## 📄 License

All rights reserved. This project is submitted as a Final Year Project (FYP) at FAST-NUCES, Chiniot-Faisalabad Campus, 2025.
