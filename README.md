# Agora v1.2 — Multi-Agent LLM Debate System

A weekend project exploring what happens when you put four frontier LLMs in a structured debate room, give each a distinct expert role, and let an independent judge decide when the conversation has produced enough value to synthesize.

Built in Python. No frameworks. Just APIs, a SQL database, and some prompt engineering.

---

## How it works

```
PRE-DEBATE
  GPT-5.4 generates 4 expert roles tailored to the specific topic.
  Roles are dynamically created — no hardcoded personas.

ROUND 1 — Blind positions (temp 0.9)
  Each analyst responds only to the topic and framework.
  They cannot see each other's answers. Maximum diversity of initial positions.
  → Round summary generated at the end.

ROUND 2 — Role-activated debate (temp 0.7)
  Each analyst reads all Round 1 responses and responds from their assigned role.
  → Round summary
  → Grok-4.2 multi-agent (judge) evaluates: SYNTHESIZE or CONTINUE

ROUND 3 — Focused debate (temp 0.6)
  Analysts receive the judge's identified tension and focus on it.
  → Round summary + judge evaluation

ROUND 4 — Final round (temp 0.6)
  If reached, synthesis is mandatory regardless of judge decision.

EXECUTIVE SYNTHESIS
  GPT-5.4 reads the full debate and writes a 3-5 paragraph executive conclusion:
  convergences, unresolved tensions, and an actionable recommendation.
  Saved to Azure SQL and exported to a timestamped TXT file.
```

---

## The cast

| Role | Model | Character |
|---|---|---|
| Analyst A | GPT-5.4 (Azure OpenAI) | Dynamically assigned |
| Analyst B | Gemini 2.5 Pro (Google) | Dynamically assigned |
| Analyst C | Claude Opus 4.7 (Anthropic) | Dynamically assigned |
| Analyst D | Grok-4.20 multi-agent (xAI) | The Disruptor — questions the premise |
| Judge | Grok-4.20 multi-agent (xAI) | Independent referee |
| Synthesizer | GPT-5.4 (Azure OpenAI) | Executive conclusion |

**Key design decisions:**
- Analysts never know which model the others are — they only see aliases (Analyst A/B/C/D)
- Round 1 is blind so models form independent positions before seeing each other
- The judge is Grok-4.2 multi-agent, a different model from the debaters, to reduce self-serving evaluation
- Maximum 4 rounds — the judge tends to want more; the cap forces synthesis

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/your-username/agora.git
cd agora
pip install openai anthropic google-generativeai pyodbc python-dotenv
```

### 2. Configure credentials

```bash
cp .env.example .env
# Edit .env with your actual API keys
```

You need accounts and API keys for:
- **Azure OpenAI** — deploy a GPT-5.4 model in your resource
- **Google AI Studio** — Gemini 2.5 Pro access
- **Anthropic** — Claude Opus 4.7
- **xAI** — Grok-4.2 multi-agent API access (console.x.ai)
- **Azure SQL** — a database with the `debate_sessions` table (schema below)

### 3. Create the database table

```sql
CREATE TABLE dbo.debate_sessions_v12 (
    id           INT              IDENTITY(1,1) PRIMARY KEY,
    session_id   UNIQUEIDENTIFIER NOT NULL,
    round_num    INT              NOT NULL,
    alias        NVARCHAR(50),
    model_name   NVARCHAR(50),
    role         NVARCHAR(50),
    response     NVARCHAR(MAX),
    created_at   DATETIME         DEFAULT GETDATE()
);
```

### 4. Set your topic

Edit `topic.txt` with your debate topic (no length limit).
Edit `framework.txt` with your analysis framework, constraints, and criteria.

---

## Run

Open `agora_v1.2.ipynb` in VS Code or Jupyter and run all cells.

Or from Python directly:

```python
from debate import run_debate
from pathlib import Path

topic    = Path("topic.txt").read_text(encoding="utf-8").strip()
framework = Path("framework.txt").read_text(encoding="utf-8").strip()

run_debate(topic, framework)
```

The debate runs automatically. A timestamped TXT file is exported when it finishes.

---

## Output format

```
AGORA v1.2 — Multi-Agent LLM Debate
============================================================
Topic / Framework / Session / Date / Close reason / Models
============================================================

ROUND 1 — BLIND POSITIONS
[ Analyst A ]  ...full response...
[ Analyst B ]  ...
[ Analyst C ]  ...
[ Analyst D ]  ...
  ROUND SUMMARY
  ...2-3 neutral sentences on what was argued...

ROUND 2 — ROLE-ACTIVATED DEBATE
...
  ROUND SUMMARY ...
  JUDGE DECISION
  Decision: CONTINUE
  Reason: ...
  Main tension: ...

EXECUTIVE SYNTHESIS
...3-5 paragraph executive conclusion...
```

---

## Project structure

```
v1.2/
├── debate.py          # All logic: clients, SQL, orchestration
├── prompts.py         # All system prompts as string constants
├── agora_v1.2.ipynb   # Notebook — configure and run
├── topic.txt           # Your debate topic (edit this)
├── framework.txt          # Your analysis framework (edit this)
├── .env.example       # Credential template
├── .env               # Your actual credentials (NOT in git)
└── README.md
```

---

## Why this exists

I wanted to see what happens when frontier models with different training data, architectures, and tendencies are forced to argue a position, read each other's reasoning, and iterate — without knowing who the others are.

The result is consistently more nuanced than asking a single model the same question. The blind first round prevents anchoring. The role-activated second round creates genuine friction. Grok as the independent judge avoids the self-serving evaluation problem. The disruptor (Analyst D) regularly questions whether the debate is even framing the problem correctly.

Built in a weekend. Pull requests welcome.

---

## License

MIT
