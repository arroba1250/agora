import os, uuid, json, re, datetime as dt
import pyodbc
from pathlib import Path
from itertools import groupby
from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAI
import google.generativeai as genai
import anthropic

from prompts import (
    SYSTEM_ROLE_GENERATOR, SYSTEM_ROUND_SUMMARY,
    SYSTEM_R1, SYSTEM_JUDGE, SYSTEM_SYNTHESIZER,
)

# Look for .env in v1.2/ first, then parent Agora/ folder
_env_path = Path(__file__).parent / ".env"
if not _env_path.exists():
    _env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path, override=True)

# ── SQL ────────────────────────────────────────────────────────
def _get_conn():
    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{os.environ['SQL_SERVER']},1433;"
        f"Database={os.environ['SQL_DATABASE']};"
        f"Uid={os.environ['SQL_USERNAME']};"
        f"Pwd={os.environ['SQL_PASSWORD']};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    for attempt in range(3):
        try:
            return pyodbc.connect(conn_str)
        except pyodbc.OperationalError as e:
            if attempt < 2:
                print(f"  [SQL] Connection failed (attempt {attempt+1}/3), retrying...")
                import time; time.sleep(2)
            else:
                raise e

def _insert(session_id, round_num, alias, model_name, role, response):
    check = "SELECT COUNT(*) FROM dbo.debate_sessions_v12 WHERE session_id=? AND round_num=? AND alias=?"
    insert_sql = """INSERT INTO dbo.debate_sessions_v12
                    (session_id, round_num, alias, model_name, role, response)
                    VALUES (?,?,?,?,?,?)"""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(check, (str(session_id), round_num, alias))
        if cur.fetchone()[0] > 0:
            print(f"  SKIP: {alias} round {round_num} already exists.")
            return
        cur.execute(insert_sql, (str(session_id), round_num, alias, model_name, role, response))
        conn.commit()
        print(f"  OK: {alias} round {round_num} saved.")

def _get_context(session_id, round_num=None) -> str:
    if round_num:
        sql = "SELECT alias, round_num, response FROM dbo.debate_sessions_v12 WHERE session_id=? AND round_num=? ORDER BY id"
        params = (str(session_id), round_num)
    else:
        sql = "SELECT alias, round_num, response FROM dbo.debate_sessions_v12 WHERE session_id=? ORDER BY round_num, id"
        params = (str(session_id),)
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
    if not rows:
        return "(no prior contributions)"
    return "\n\n---\n\n".join(f"[Round {r} - {a}]\n{resp}" for a, r, resp in rows)

# ── LLM Clients ────────────────────────────────────────────────
_gpt_client = AzureOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_KEY"],
    api_version="2025-03-01-preview",
)
_GPT_MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4.1")

genai.configure(api_key=os.environ["GOOGLE_API_KEY"])
_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

_claude_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
_CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-7")

_grok_client = OpenAI(
    api_key=os.environ["XAI_API_KEY"],
    base_url="https://api.x.ai/v1",
)
_GROK_MODEL = os.environ.get("GROK_MODEL", "grok-3")

# ── Model call functions ───────────────────────────────────────
def call_gpt(system_prompt, user_prompt, temperature=0.7):
    r = _gpt_client.responses.create(
        model=_GPT_MODEL,
        instructions=system_prompt,
        input=user_prompt,
        temperature=temperature,
    )
    return r.output_text.strip()

def call_gemini(system_prompt, user_prompt, temperature=0.7):
    model = genai.GenerativeModel(
        model_name=_GEMINI_MODEL,
        system_instruction=system_prompt,
        generation_config=genai.GenerationConfig(temperature=temperature),
    )
    return model.generate_content(user_prompt).text.strip()

def call_claude(system_prompt, user_prompt, temperature=0.7):
    msg = _claude_client.messages.create(
        model=_CLAUDE_MODEL, max_tokens=2048,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return msg.content[0].text.strip()

def call_grok(system_prompt, user_prompt, temperature=0.7):
    r = _grok_client.chat.completions.create(
        model=_GROK_MODEL, temperature=temperature,
        messages=[{"role": "system", "content": system_prompt},
                  {"role": "user", "content": user_prompt}],
    )
    return r.choices[0].message.content.strip()

_ANALYSTS = [
    ("Analyst A", _GPT_MODEL,    call_gpt),
    ("Analyst B", _GEMINI_MODEL, call_gemini),
    ("Analyst C", _CLAUDE_MODEL, call_claude),
    ("Analyst D", _GROK_MODEL,   call_grok),
]

# ── Dynamic role generation ────────────────────────────────────
def generate_roles(topic: str) -> dict:
    prompt = f"Debate topic:\n{topic}\n\nDefine the 4 expert profiles for this debate."
    raw = call_gpt(SYSTEM_ROLE_GENERATOR, prompt, temperature=0.8)
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        raise ValueError(f"generate_roles did not return valid JSON:\n{raw}")
    json_str = m.group()
    try:
        roles = json.loads(json_str)
    except json.JSONDecodeError:
        roles = json.loads(re.sub(r'\n\s*', ' ', json_str))
    print("Roles generated for this debate:")
    for alias, desc in roles.items():
        print(f"  {alias}: {desc[:90]}...")
    return roles

# ── Internal orchestration ─────────────────────────────────────
def _run_round(session_id, round_num, systems, prompt_rn, temp):
    print(f"\n{'='*55}\nROUND {round_num}\n{'='*55}")
    for (alias, model, fn), system in zip(_ANALYSTS, systems):
        print(f"\n  [{alias}] generating response...")
        resp = fn(system, prompt_rn, temp)
        _insert(session_id, round_num, alias, model, "debater", resp)
        print(f"{resp[:300]}...\n")

def _summarize_round(session_id, round_num):
    context = _get_context(session_id, round_num=round_num)
    prompt = f"Round {round_num} of the debate:\n{context}\n\nSummarize this round."
    print(f"\n  [Summary R{round_num}] generating...")
    summary = call_gpt(SYSTEM_ROUND_SUMMARY, prompt, temperature=0.3)
    _insert(session_id, round_num, "[Summary]", _GPT_MODEL, "summary", summary)
    print(f"  {summary[:150]}...")

def _call_judge(session_id, round_num, topic, is_final):
    suffix = "\n\nThis is the LAST round allowed. You MUST respond SYNTHESIZE." if is_final else ""
    context = _get_context(session_id)
    prompt = f"Topic: {topic}\n\nFull debate:\n{context}\n\nEvaluate and decide."
    print(f"\n  [Judge R{round_num}] evaluating debate...")
    raw = call_grok(SYSTEM_JUDGE + suffix, prompt, temperature=0.2)
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        raise ValueError(f"Invalid JSON from judge:\n{raw}")
    try:
        v = json.loads(m.group())
    except json.JSONDecodeError:
        v = json.loads(re.sub(r'\n\s*', ' ', m.group()))
    print(f"  → Decision:     {v['decision']}")
    print(f"  → Reason:       {v['reason']}")
    if v.get("main_tension"):
        print(f"  → Main tension: {v['main_tension']}")
    verdict_txt = f"Decision: {v['decision']}\nReason: {v['reason']}"
    if v.get("main_tension"):
        verdict_txt += f"\nMain tension: {v['main_tension']}"
    _insert(session_id, round_num, "[Judge]", _GROK_MODEL, "verdict", verdict_txt)
    return v

# ── TXT export formatting ──────────────────────────────────────
_PHASE_NAMES = {
    1:  "ROUND 1 — BLIND POSITIONS",
    2:  "ROUND 2 — ROLE-ACTIVATED DEBATE",
    3:  "ROUND 3 — FOCUSED DEBATE",
    4:  "ROUND 4 — FINAL ROUND",
    99: "EXECUTIVE SYNTHESIS",
}

def _format_export(session_id) -> str:
    sql = "SELECT alias, round_num, response FROM dbo.debate_sessions_v12 WHERE session_id=? ORDER BY round_num, id"
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, (str(session_id),))
        rows = cur.fetchall()
    if not rows:
        return "(no content)"
    blocks = []
    for round_num, entries in groupby(rows, key=lambda x: x[1]):
        title = _PHASE_NAMES.get(round_num, f"ROUND {round_num}")
        sep, thin = "=" * 60, "- " * 30
        block = f"\n{sep}\n{title}\n{sep}\n"
        for alias, _, response in entries:
            if alias == "[Summary]":
                block += f"\n  ROUND SUMMARY\n  {thin}\n  {response}\n"
            elif alias == "[Judge]":
                block += f"\n  JUDGE DECISION\n  {thin}\n"
                for line in response.splitlines():
                    block += f"  {line}\n"
                block += "\n"
            else:
                block += f"\n[ {alias} ]\n\n{response}\n\n{thin}\n"
        blocks.append(block)
    return "\n".join(blocks)

# ── Public entry point ─────────────────────────────────────────
def run_debate(topic: str, framework: str, max_rounds: int = 4) -> str:
    session_id = uuid.uuid4()
    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"Session:    {session_id}")
    print(f"Topic:      {topic[:70]}...")
    print(f"Max rounds: {max_rounds}\n")

    roles = generate_roles(topic)

    # Round 1 — blind positions, no roles activated
    prompt_r1 = f"Topic: {topic}\nAnalysis framework: {framework}\n\nGive your initial analysis on the topic."
    _run_round(session_id, 1, [SYSTEM_R1] * 4, prompt_r1, temp=0.9)
    _summarize_round(session_id, 1)

    # Rounds 2 to max_rounds — roles activated, tension-focused
    tension = None
    close_reason = f"max rounds limit ({max_rounds}) reached"
    for round_num in range(2, max_rounds + 1):
        is_final = (round_num == max_rounds)
        prev_context = _get_context(session_id)
        tension_str = (
            f'\n\nUnresolved tension flagged by the judge:\n"{tension}"\nFocus your response on it.'
            if tension else ""
        )
        prompt_rn = (
            f"Topic: {topic}\nAnalysis framework: {framework}\n\n"
            f"Debate so far:\n{prev_context}"
            f"{tension_str}\n\nRespond from your position. Be precise and direct."
        )
        temp = 0.7 if round_num == 2 else 0.6
        _run_round(
            session_id, round_num,
            [roles["Analyst A"], roles["Analyst B"], roles["Analyst C"], roles["Analyst D"]],
            prompt_rn, temp,
        )
        _summarize_round(session_id, round_num)

        verdict = _call_judge(session_id, round_num, topic, is_final=is_final)
        tension = verdict.get("main_tension")

        if verdict["decision"] == "SYNTHESIZE":
            close_reason = f"judge decided SYNTHESIZE after round {round_num}"
            print(f"\n  Debate closed at round {round_num} — {close_reason}")
            break
        elif is_final:
            print(f"\n  Round {round_num} is the last allowed — moving to mandatory synthesis")

    # Executive synthesis
    print("\n" + "=" * 55)
    print("EXECUTIVE SYNTHESIS")
    print(f"Close reason: {close_reason}")
    print("=" * 55 + "\n")
    final_context = _get_context(session_id)
    synthesis = call_gpt(SYSTEM_SYNTHESIZER, f"Full debate:\n{final_context}", temperature=0.3)
    _insert(session_id, 99, "[Synthesis]", _GPT_MODEL, "synthesizer", synthesis)
    print(synthesis)

    # Export TXT (utf-8-sig so Windows reads accents correctly)
    output_path = Path(__file__).parent / f"debate_{timestamp}.txt"
    header = (
        f"AGORA v1.2 — Multi-Agent LLM Debate\n{'=' * 60}\n"
        f"Topic:     {topic}\n"
        f"Framework: {framework[:120]}...\n"
        f"Session:   {session_id}\n"
        f"Date:      {timestamp}\n"
        f"Closed:    {close_reason}\n"
        f"Models:    A={_GPT_MODEL} | B={_GEMINI_MODEL} | C={_CLAUDE_MODEL} | D={_GROK_MODEL}\n"
        f"Judge:     {_GROK_MODEL}\n"
        f"{'=' * 60}\n"
    )
    output_path.write_text(header + _format_export(session_id), encoding="utf-8-sig")
    print(f"\nSaved to: {output_path}")
    return str(output_path)
