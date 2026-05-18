# ============================================================
# Gradio UI — three modes
#   Tab 1 "Ask question"               — chat code review with skill tools
#   Tab 2 "Review my project"          — paste GitHub link → stack detect → AI review
#   Tab 3 "Full pipeline (Layers 0-4)" — local path / clone → 0/1/2 + 3 + 4 (if JD)
# ============================================================
# Run:
#   .venv\Scripts\activate
#   python app.py
# Then open http://127.0.0.1:7860
# ============================================================

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import traceback
from pathlib import Path
from typing import Iterator

import gradio as gr
from dotenv import load_dotenv

import skills
import detect_stack as ds
from llm_client import DEFAULT_MODELS, PROVIDERS, get_sync_client, resolve_model

load_dotenv()

DEFAULT_PROVIDER = (os.environ.get("LLM_PROVIDER") or "openai").lower()
TEMPERATURE    = 0
MAX_TOKENS     = 800
MAX_TOOL_ITER  = 4

SYSTEM_PROMPT = """You are a senior software engineer performing code review.
Respond with concise prose. Cite WHY, give a concrete fix.

You have four tools:
- list_skills(): locally-registered skill packs (always preferred).
- fetch_skill(name, file?): read a local skill's content.
- search_skills_catalog(query): search the EXTERNAL mindrally/skills catalog (240+ community skills, e.g. fastapi-python, django-python, flask-python, rust, prisma, drizzle-orm, supabase, tailwindcss, kubernetes, docker, postgresql-best-practices, etc.) when no local skill matches.
- fetch_external_skill(name): fetch a SKILL.md from the external catalog after locating it via search.

Tool order (MANDATORY — do not skip steps):
1. ALWAYS call list_skills first. If a local skill clearly matches the topic, fetch_skill it.
2. If NO local skill matches, you MUST call search_skills_catalog with a topic keyword (e.g. "fastapi", "rust", "prisma", "django") BEFORE writing any review. Do NOT fall back to training knowledge until you have searched.
   - Pick the keyword from the framework/library/language in the snippet.
   - If search returns names, call fetch_external_skill on the most relevant one.
3. Only after step 2 returns empty may you rely on training knowledge.
4. Every final answer must state: "Skill used: <name>" or "Skill used: none (catalog had no match)".

Skill version scope:
- The `nextjs16` skill ONLY covers Next.js 16. Do NOT fetch it for older versions.
- If the project / snippet uses Next.js < 16 (v15, v14, v13, etc.), rely on your own training knowledge instead. Mention the version assumption in your answer.
- When the user does not state the version, infer from APIs used (e.g. async `params`, `proxy` middleware → v16; `pages/` router only → v12 or earlier).
- Same rule for any future version-scoped skills: only fetch when the project version matches the skill's stated version range."""

# Pre-pull skills once on import
print("Pre-pulling skill packs…")
skills.init()
print(f"Loaded: {[s['name'] for s in skills.list_skills()]}")


# ── Shared helpers ───────────────────────────────────────────

def _msg(role: str, content: str, title: str | None = None, status: str = "done") -> dict:
    m: dict = {"role": role, "content": content}
    if title:
        m["metadata"] = {"title": title, "status": status}
    return m


def _tool_loop_stream(
    oa_messages: list[dict],
    provider: str = DEFAULT_PROVIDER,
    model: str | None = None,
) -> Iterator[tuple[str, dict | None, str]]:
    """Generator yielding ('event_kind', payload, final_text).

    event_kind:
      'tool_pending'  → payload={'name','args'}
      'tool_done'     → payload={'name','args','result','result_len'}
      'final'         → final_text set
      'error'         → final_text=err
    """
    called: set[str] = set()
    fetched_local = False
    force_search = False
    client = get_sync_client(provider)
    eff_model = resolve_model(provider, model)

    for step in range(MAX_TOOL_ITER):
        kwargs = dict(
            model=eff_model,
            temperature=TEMPERATURE,
            max_completion_tokens=MAX_TOKENS,
            tools=skills.TOOL_SCHEMAS,
            messages=oa_messages,
        )
        if force_search:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": "search_skills_catalog"},
            }
            force_search = False
        response = client.chat.completions.create(**kwargs)
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            # Enforce step 2: if list_skills ran but search never tried and no local skill was fetched,
            # rewind and force a search_skills_catalog call.
            if ("list_skills" in called
                and "search_skills_catalog" not in called
                and not fetched_local
                and step < MAX_TOOL_ITER - 1):
                oa_messages.append({
                    "role": "user",
                    "content": (
                        "You skipped step 2. No local skill was fetched. "
                        "Call search_skills_catalog now with a topic keyword "
                        "from the user's snippet (e.g. 'fastapi', 'django')."
                    ),
                })
                force_search = True
                continue

            usage  = response.usage
            footer = f"\n\n---\n_{usage.prompt_tokens}→{usage.completion_tokens} tok_"
            yield ("final", {"usage": usage}, (msg.content or "(empty)") + footer)
            return

        oa_messages.append({
            "role":       "assistant",
            "content":    msg.content or "",
            "tool_calls": [
                {
                    "id":   c.id,
                    "type": "function",
                    "function": {
                        "name":      c.function.name,
                        "arguments": c.function.arguments or "{}",
                    },
                }
                for c in tool_calls
            ],
        })

        for c in tool_calls:
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            yield ("tool_pending", {"name": c.function.name, "args": args, "id": c.id}, "")
            result = skills.dispatch(c.function.name, args)
            called.add(c.function.name)
            if c.function.name == "fetch_skill":
                fetched_local = True
            yield ("tool_done",
                   {"name": c.function.name, "args": args, "id": c.id,
                    "result": result, "result_len": len(result)}, "")
            oa_messages.append({
                "role":         "tool",
                "tool_call_id": c.id,
                "content":      result,
            })

    yield ("final", None, "_hit MAX_TOOL_ITER without final answer_")


# ── Tab 1: chat ──────────────────────────────────────────────

def chat_fn(
    user_msg: str,
    history: list[dict],
    provider: str = DEFAULT_PROVIDER,
    model: str = "",
) -> Iterator[list[dict]]:
    oa_messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in history:
        if m.get("metadata"):
            continue
        oa_messages.append({"role": m["role"], "content": m["content"]})
    oa_messages.append({"role": "user", "content": user_msg})

    new_msgs: list[dict] = []
    start = time.time()

    try:
        pending_idx: dict[str, int] = {}
        for kind, payload, text in _tool_loop_stream(
            oa_messages, provider=provider, model=model or None,
        ):
            if kind == "tool_pending":
                new_msgs.append(_msg(
                    "assistant",
                    f"Calling `{payload['name']}` with `{json.dumps(payload['args'])}`…",
                    title=f"🔧 {payload['name']}",
                    status="pending",
                ))
                pending_idx[payload["id"]] = len(new_msgs) - 1
                yield new_msgs

            elif kind == "tool_done":
                idx = pending_idx.get(payload["id"], len(new_msgs) - 1)
                preview = payload["result"][:1500] + (
                    "\n…[truncated]" if payload["result_len"] > 1500 else ""
                )
                new_msgs[idx] = _msg(
                    "assistant",
                    f"**Args:** `{json.dumps(payload['args'])}`\n\n"
                    f"**Result ({payload['result_len']} chars):**\n```\n{preview}\n```",
                    title=f"🔧 {payload['name']}",
                    status="done",
                )
                yield new_msgs

            elif kind == "final":
                elapsed = (time.time() - start) * 1000
                final_text = text.replace("_tok_", f"tok · {elapsed:.0f} ms_")
                new_msgs.append(_msg("assistant", final_text))
                yield new_msgs
                return

    except Exception as e:
        new_msgs.append(_msg("assistant", f"**ERROR**\n```\n{e}\n```"))
        yield new_msgs


# ── Tab 2: project review ────────────────────────────────────

CODE_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
             ".py", ".go", ".rs", ".rb", ".java", ".kt",
             ".cs", ".php", ".vue", ".svelte", ".c", ".cpp", ".h"}

SKIP_RE = re.compile(
    r"(^|/)("
    r"node_modules|dist|build|out|\.next|\.nuxt|target|vendor|"
    r"__pycache__|\.venv|venv|coverage|\.git"
    r")/|"
    r"\.test\.|\.spec\.|\.min\.|\.d\.ts$"
)

ENTRY_HINTS = (
    "page.tsx", "page.ts", "layout.tsx", "layout.ts",
    "app.component.ts", "main.ts", "main.py", "app.py",
    "index.ts", "index.tsx", "index.js", "server.ts",
)


def _pick_review_files(paths: list[str], subdir: str, limit: int = 5) -> list[str]:
    prefix = subdir.rstrip("/") + "/" if subdir else ""
    pool = [p for p in paths if p.startswith(prefix)]

    candidates = [
        p for p in pool
        if any(p.endswith(e) for e in CODE_EXTS) and not SKIP_RE.search(p)
    ]

    def score(p: str) -> tuple:
        leaf  = p.rsplit("/", 1)[-1].lower()
        entry = 0 if any(leaf.endswith(h) for h in ENTRY_HINTS) else 1
        depth = p.count("/")
        return (entry, depth, len(p))

    candidates.sort(key=score)
    return candidates[:limit]


def _detection_md(ref: ds.RepoRef, d: ds.Detection,
                  suggested: list[str], notes: list[str]) -> str:
    rows = []
    if d.languages:   rows.append(f"- **Languages**: {', '.join(sorted(d.languages))}")
    if d.frameworks:  rows.append(f"- **Frameworks**: {', '.join(sorted(d.frameworks))}")
    if d.versions:    rows.append(f"- **Versions**: " + ", ".join(
                          f"{t} `{v}`" for t, v in sorted(d.versions.items())))
    if d.package_mgr: rows.append(f"- **Package manager**: {', '.join(sorted(d.package_mgr))}")
    if d.databases:   rows.append(f"- **Databases**: {', '.join(sorted(d.databases))}")
    if d.testing:     rows.append(f"- **Testing**: {', '.join(sorted(d.testing))}")
    if d.infra:       rows.append(f"- **Infra**: {', '.join(sorted(d.infra))}")
    body = "\n".join(rows) or "_(no signals detected)_"

    skill_md = ""
    if suggested:
        skill_md += f"\n\n**Skills to use**: `{', '.join(suggested)}`"
    for n in notes:
        skill_md += f"\n- _{n}_"
    if not suggested and not notes:
        skill_md = "\n\n_No registered skill matches this stack — model will use its own training knowledge._"

    return f"### Detected stack — `{ref.owner}/{ref.repo}` ({ref.subdir or '/'})\n{body}{skill_md}"


def _build_review_prompt(ref: ds.RepoRef, d: ds.Detection,
                         suggested: list[str], notes: list[str],
                         file_blobs: list[tuple[str, str]]) -> str:
    versions_str = ", ".join(f"{t}={v}" for t, v in d.versions.items()) or "(unknown)"
    skill_hint = (
        f"Available skills for this stack: {suggested}. Use the fetch_skill tool."
        if suggested else
        "No matching skill — use your training knowledge. " + " ".join(notes)
    )
    files_blob = "\n\n".join(
        f"=== {path} ===\n```\n{body}\n```" for path, body in file_blobs
    )
    return f"""Review the project `{ref.owner}/{ref.repo}` (path `{ref.subdir or '/'}`).

Detected stack:
- Languages:  {', '.join(sorted(d.languages)) or '?'}
- Frameworks: {', '.join(sorted(d.frameworks)) or '?'}
- Versions:   {versions_str}

{skill_hint}

Below are {len(file_blobs)} key source files (truncated). Identify the top 3-5 issues across security, correctness, design, performance. For each: file:line, the issue, why, concrete fix. End with an overall verdict (1-2 sentences).

{files_blob}"""


def review_project_stream(
    url: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = "",
) -> Iterator[str]:
    if not url.strip():
        yield "Enter a GitHub URL."
        return

    log = "### Step 1 — parse URL\n"
    yield log

    try:
        ref = ds.parse_url(url)
    except ValueError as e:
        yield log + f"\n**Error**: {e}"
        return
    log += f"Target: `{ref.owner}/{ref.repo}` subdir `{ref.subdir or '/'}`\n\n### Step 2 — list repo tree\n"
    yield log

    try:
        branch = ds.resolve_default_branch(ref)
        paths  = ds.list_tree(ref, branch)
    except Exception as e:
        yield log + f"\n**GitHub API error**: {e}"
        return
    log += f"Branch `{branch}`, {len(paths)} files\n\n### Step 3 — detect stack\n"
    yield log

    try:
        d = ds.detect(ref)
        suggested, notes = ds.suggest_skills(d)
    except Exception as e:
        yield log + f"\n**Detector error**: {e}"
        return

    log += _detection_md(ref, d, suggested, notes) + "\n\n### Step 4 — fetch key source files\n"
    yield log

    files = _pick_review_files(paths, ref.subdir, limit=5)
    file_blobs: list[tuple[str, str]] = []
    for fp in files:
        body = ds.fetch_file(ref, branch, fp) or ""
        truncated = body[:3000] + ("\n…[truncated]" if len(body) > 3000 else "")
        file_blobs.append((fp, truncated))
        log += f"- `{fp}` ({len(body)} chars)\n"
        yield log

    if not file_blobs:
        yield log + "\n_no source files matched the review heuristic_"
        return

    log += "\n### Step 5 — AI review\n_calling model…_\n"
    yield log

    oa_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": _build_review_prompt(ref, d, suggested, notes, file_blobs)},
    ]

    start = time.time()
    try:
        for kind, payload, text in _tool_loop_stream(
            oa_messages, provider=provider, model=model or None,
        ):
            if kind == "tool_pending":
                log += f"- tool call: `{payload['name']}({json.dumps(payload['args'])})`\n"
                yield log
            elif kind == "tool_done":
                preview = payload["result"][:300].replace("\n", " ⏎ ")
                log += f"  - result ({payload['result_len']} chars): `{preview}…`\n"
                yield log
            elif kind == "final":
                elapsed = (time.time() - start) * 1000
                log = log.replace("_calling model…_\n", "")
                log += f"\n#### Review ({elapsed:.0f} ms)\n{text}\n"
                yield log
                return
    except Exception as e:
        yield log + f"\n**Model error**\n```\n{e}\n{traceback.format_exc()}\n```"


# ── Tab 3: full pipeline review (Layers 0-3) ─────────────────

def _format_layer3_md(l3: dict) -> str:
    parts = ["## Layer 3 — AI Context Review"]
    parts.append(
        f"_Model: `{l3.get('model','?')}` · "
        f"{l3.get('elapsed_ms', '?')} ms · "
        f"dropped {len(l3.get('dropped_finding_ids', []))} bogus finding_ids_\n"
    )

    a = l3.get("call_a_architecture") or {}
    parts.append("### Call A — Architecture")
    if "error" in a:
        parts.append(f"_error: {a['error']}_")
    else:
        if a.get("scale_assessment"):
            parts.append(f"**Scale:** {a['scale_assessment']}")
        if a.get("patterns_observed"):
            parts.append("**Patterns:** " + ", ".join(a["patterns_observed"]))
        if a.get("anti_patterns"):
            parts.append("**Anti-patterns:**")
            for ap in a["anti_patterns"]:
                parts.append(
                    f"- `{ap.get('evidence_file','?')}` — **{ap.get('name','?')}**: "
                    f"{ap.get('explanation','')}"
                )
        if a.get("recommendations"):
            parts.append("**Recommendations:**")
            for r in a["recommendations"]:
                parts.append(f"- {r}")

    b = l3.get("call_b_jobfit")
    if b is not None:
        parts.append("\n### Call B — Job Fit")
        if "error" in b:
            parts.append(f"_error: {b['error']}_")
        else:
            if b.get("covered_skills"):
                parts.append("**Covered:**")
                for s in b["covered_skills"]:
                    parts.append(f"- {s.get('skill','?')} — `{s.get('evidence_file','?')}`")
            if b.get("missing_skills"):
                parts.append("**Missing:**")
                for s in b["missing_skills"]:
                    parts.append(f"- {s.get('skill','?')} — _{s.get('why_jd_needs_it','')}_")
            if b.get("next_project_suggestion"):
                parts.append(f"**Next project:** {b['next_project_suggestion']}")
    else:
        parts.append("\n### Call B — Job Fit\n_skipped (no JD provided)_")

    c = l3.get("call_c_teaching") or {}
    parts.append("\n### Call C — Teaching")
    if "error" in c:
        parts.append(f"_error: {c['error']}_")
    else:
        for e in c.get("explanations") or []:
            parts.append(
                f"- **[{e.get('severity','?')}]** `{e.get('finding_id','?')}` "
                f"→ {e.get('why_it_matters','')} _(ref: {e.get('reference','n/a')})_"
            )
        if c.get("overall_tone_note"):
            parts.append(f"\n_{c['overall_tone_note']}_")
    return "\n\n".join(parts)


def _format_layer4_md(s: dict) -> str:
    if not s:
        return "_(empty suggestion)_"
    if "error" in s:
        return f"**Error from model:** {s.get('error')}\n\n```\n{s.get('raw','')}\n```"

    parts: list[str] = []
    title = s.get("project_title", "(untitled project)")
    parts.append(f"# {title}")
    if s.get("problem_statement"):
        parts.append(f"**Problem:** {s['problem_statement']}")

    if s.get("must_have_features"):
        parts.append("## Must-have features")
        for f in s["must_have_features"]:
            parts.append(f"- {f}")

    if s.get("stretch_goals"):
        parts.append("## Stretch goals")
        for f in s["stretch_goals"]:
            parts.append(f"- {f}")

    if s.get("recommended_stack"):
        parts.append("## Recommended stack")
        for t in s["recommended_stack"]:
            t = str(t)
            if " — because " in t:
                tech, reason = t.split(" — because ", 1)
                parts.append(f"- **{tech.strip()}** — because {reason.strip()}")
            elif " - because " in t:
                tech, reason = t.split(" - because ", 1)
                parts.append(f"- **{tech.strip()}** — because {reason.strip()}")
            else:
                parts.append(f"- **{t}**")

    skills = s.get("skills_demonstrated") or []
    if skills:
        parts.append("## Skills the JD asked for")
        parts.append("| Skill | Quoted from JD |")
        parts.append("|---|---|")
        for row in skills:
            sk = (row.get("skill") or "").replace("|", "\\|")
            fj = (row.get("from_jd") or "").replace("|", "\\|")
            parts.append(f"| {sk} | _{fj}_ |")

    rubric = s.get("success_rubric") or []
    if rubric:
        parts.append("## How a reviewer will grade it")
        parts.append("| Criterion | Measure |")
        parts.append("|---|---|")
        for row in rubric:
            cr = (row.get("criterion") or "").replace("|", "\\|")
            me = (row.get("measure") or "").replace("|", "\\|")
            parts.append(f"| {cr} | {me} |")

    inspired = s.get("inspired_by") or []
    if inspired:
        parts.append("## Inspired by")
        parts.append("| Feature | Sources |")
        parts.append("|---|---|")
        for row in inspired:
            feat = (row.get("feature") or "").replace("|", "\\|")
            ids = ", ".join(row.get("source_ids") or [])
            parts.append(f"| {feat} | {ids} |")

    sources = s.get("research_sources") or []
    if sources:
        parts.append("## Research sources")
        parts.append("| ID | Platform | Title | Pain point | Detected stack |")
        parts.append("|---|---|---|---|---|")
        for src in sources:
            sid = (src.get("id") or "").replace("|", "\\|")
            plat = (src.get("platform") or "").replace("|", "\\|")
            title = (src.get("title") or "").replace("|", "\\|")
            url = src.get("url") or ""
            pp = (src.get("pain_point") or "").replace("|", "\\|")
            title_md = f"[{title}]({url})" if url else title
            gs = src.get("github_stack") or {}
            chips: list[str] = []
            for bucket in ("frameworks", "languages", "databases", "infra"):
                chips.extend(gs.get(bucket) or [])
            stack_md = ", ".join(chips[:6]).replace("|", "\\|") if chips else ""
            parts.append(
                f"| {sid} | {plat} | {title_md} | {pp} | {stack_md} |"
            )

    return "\n\n".join(parts)


def _shallow_clone(url: str, dest: Path) -> None:
    if not shutil.which("git"):
        raise RuntimeError("git not on PATH — cannot clone remote repo")
    proc = subprocess.run(
        ["git", "clone", "--depth", "1", url, str(dest)],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:300]}")


def full_review_stream(
    target: str,
    jd: str,
    provider: str = DEFAULT_PROVIDER,
    model: str = "",
) -> Iterator[str]:
    target = (target or "").strip()
    jd = jd or ""
    eff_model = model or None
    if not target and not jd.strip():
        yield "Enter a local path / GitHub URL, or paste a JD, or both."
        return

    # JD-only mode: skip Layers 0/1/2/3, run Layer 4 directly
    if not target:
        log = (
            "### JD-only mode\n"
            "_No repo provided — skipping Layers 0/1/2/3, running Layer 4 only._\n\n"
            "### Step 4a — Layer 4a (Web Research)\n"
            "_searching Reddit + Hacker News for related pain points…_\n"
            "\n### Step 4 — Layer 4 (Project Suggestion from JD)\n"
            "_calling model…_\n"
        )
        yield log

        from project_suggester import log_run as log_run_l4
        from project_suggester import run_layer4_sync

        try:
            layer4 = run_layer4_sync(jd, provider=provider, model=eff_model)
            log = log.replace(
                "_searching Reddit + Hacker News for related pain points…_\n", "",
            )
            log = log.replace("_calling model…_\n", "")
            r = layer4.research
            platforms = sorted({s.platform for s in r.sources if s.platform})
            log += (
                f"\n**Research:** {len(r.sources)} source(s) from "
                f"{', '.join(platforms) if platforms else 'no platforms'} · "
                f"queries: {', '.join(f'`{q}`' for q in r.queries) or 'none'}\n"
            )
            log += "\n" + _format_layer4_md(layer4.suggestion)
            try:
                rec4 = log_run_l4(jd, layer4,
                                  log_root=Path("results/layer4_logs"))
                log += (
                    f"\n\n---\n**Layer 4 eval log saved** · "
                    f"`run_id={rec4['run_id']}` · "
                    f"{layer4.elapsed_ms} ms · "
                    f"{layer4.usage.prompt_tokens}+"
                    f"{layer4.usage.completion_tokens} tokens\n"
                    f"- input: `{rec4['input_path']}`\n"
                    f"- output: `{rec4['output_path']}`\n"
                    f"- research: `{rec4['research_path']}`\n"
                    f"- index: `results/layer4_logs/index.jsonl`"
                )
            except Exception as log_err:
                log += f"\n\n_layer4 log_run failed: {log_err}_"
        except Exception as l4_err:
            log += f"\n\n**Layer 4 error**\n```\n{l4_err}\n```"
        yield log
        return

    log = "### Step 1 — locate project\n"
    yield log

    tmp: tempfile.TemporaryDirectory | None = None
    try:
        if target.startswith(("http://", "https://", "git@")):
            tmp = tempfile.TemporaryDirectory(prefix="reviewme_")
            root = Path(tmp.name) / "repo"
            log += f"Cloning `{target}` …\n"
            yield log
            _shallow_clone(target, root)
        else:
            root = Path(target).resolve()
            if not root.exists():
                yield log + f"\n**Error**: path not found: `{root}`"
                return
        log += f"Project root: `{root}`\n\n### Step 2 — run Layers 0/1/2\n"
        yield log

        from analyze import analyze
        from ai_reviewer import run_layer3_sync, log_run

        analysis = analyze(root, include_deps=True)
        l1 = analysis.get("layer1_static", {}).get("summary", {})
        l2 = analysis.get("layer2_ast", {}).get("summary", {})
        l0 = analysis.get("layer0_deps", {}).get("summary", {})
        log += (
            f"- Layer 0 vulns: {l0.get('total_vulns', 0)}\n"
            f"- Layer 1 findings: {l1.get('total', 0)} "
            f"(by sev: {l1.get('by_severity', {})})\n"
            f"- Layer 2 files: {analysis.get('layer2_ast', {}).get('file_count', 0)} "
            f"max_cc={l2.get('max_cyclomatic', 0)}\n\n"
            "### Step 3 — Layer 3 (AI Context Review)\n_running 3 calls in parallel…_\n"
        )
        yield log

        layer3 = run_layer3_sync(analysis, jd, provider=provider, model=eff_model)
        log = log.replace("_running 3 calls in parallel…_\n", "")
        log += "\n" + _format_layer3_md(layer3.to_dict())

        try:
            rec = log_run(analysis, jd, layer3, target=str(target),
                          log_root=Path("results/layer3_logs"))
            log += (
                f"\n\n---\n**Layer 3 eval log saved** · `run_id={rec['run_id']}`\n"
                f"- input: `{rec['input_path']}`\n"
                f"- output: `{rec['output_path']}`\n"
                f"- index: `results/layer3_logs/index.jsonl`"
            )
        except Exception as log_err:
            log += f"\n\n_layer3 log_run failed: {log_err}_"
        yield log

        # ── Layer 4 — project suggestion (only if JD provided) ──
        if jd.strip():
            log += (
                "\n\n### Step 4a — Layer 4a (Web Research)\n"
                "_searching Reddit + Hacker News for related pain points…_\n"
                "\n### Step 4 — Layer 4 (Project Suggestion from JD)\n"
                "_calling model…_\n"
            )
            yield log

            from project_suggester import log_run as log_run_l4
            from project_suggester import run_layer4_sync

            try:
                layer4 = run_layer4_sync(jd, provider=provider, model=eff_model)
                log = log.replace(
                    "_searching Reddit + Hacker News for related pain points…_\n",
                    "",
                )
                log = log.replace("_calling model…_\n", "")
                r = layer4.research
                platforms = sorted({s.platform for s in r.sources if s.platform})
                log += (
                    f"\n**Research:** {len(r.sources)} source(s) from "
                    f"{', '.join(platforms) if platforms else 'no platforms'} · "
                    f"queries: {', '.join(f'`{q}`' for q in r.queries) or 'none'}\n"
                )
                log += "\n" + _format_layer4_md(layer4.suggestion)
                try:
                    rec4 = log_run_l4(jd, layer4,
                                      log_root=Path("results/layer4_logs"))
                    log += (
                        f"\n\n---\n**Layer 4 eval log saved** · "
                        f"`run_id={rec4['run_id']}` · "
                        f"{layer4.elapsed_ms} ms · "
                        f"{layer4.usage.prompt_tokens}+"
                        f"{layer4.usage.completion_tokens} tokens\n"
                        f"- input: `{rec4['input_path']}`\n"
                        f"- output: `{rec4['output_path']}`\n"
                        f"- research: `{rec4['research_path']}`\n"
                        f"- index: `results/layer4_logs/index.jsonl`"
                    )
                except Exception as log_err:
                    log += f"\n\n_layer4 log_run failed: {log_err}_"
            except Exception as l4_err:
                log += f"\n\n**Layer 4 error**\n```\n{l4_err}\n```"
            yield log
        else:
            log += (
                "\n\n### Step 4 — Layer 4 (Project Suggestion)\n"
                "_skipped (no JD provided)_"
            )
            yield log

    except Exception as e:
        yield log + f"\n**ERROR**\n```\n{e}\n{traceback.format_exc()}\n```"
    finally:
        if tmp is not None:
            tmp.cleanup()


# ── UI ───────────────────────────────────────────────────────

EXAMPLES = [
    [
        "Review this Next.js 16 page:\n"
        "```tsx\n"
        "export default function DashboardPage({ params }) {\n"
        "  const id = params.userId;\n"
        "  return <div>Dashboard for {id}</div>;\n"
        "}\n"
        "```"
    ],
    [
        "Is this idiomatic Angular?\n"
        "```ts\n"
        "@Component({ selector: 'app-counter', standalone: true,\n"
        "  template: `{{count()}} <button (click)=\"inc()\">+</button>` })\n"
        "export class Counter {\n"
        "  count = signal(0);\n"
        "  inc() { this.count.update(v => v + 1); }\n"
        "}\n"
        "```"
    ],
]

MODEL_CHOICES = [
    "",  # blank = use default for selected provider
    # OpenAI
    "gpt-5.4-mini",
    "gpt-4o-mini",
    "gpt-4o",
    "o3-mini",
    # Ollama — local (verified: tool_calls + JSON mode + tool loop all pass)
    "qwen3:8b",          # 5.2GB · best balance, fits VRAM
    "qwen3:4b",          # 2.5GB · smaller
    "qwen3.5:0.8b",      # 1.0GB · tiny but functional
    # Ollama — cloud (Ollama Turbo, no subscription required)
    "gpt-oss:120b-cloud",
    "gpt-oss:20b-cloud",
]


def _provider_row(
    prefix: str, model_as_dropdown: bool = False
) -> tuple[gr.Dropdown, gr.Component]:
    """Build a shared provider+model row. Returns the two components."""
    with gr.Row():
        prov = gr.Dropdown(
            choices=list(PROVIDERS),
            value=DEFAULT_PROVIDER if DEFAULT_PROVIDER in PROVIDERS else "openai",
            label=f"{prefix} provider",
            scale=1,
        )
        if model_as_dropdown:
            mdl = gr.Dropdown(
                choices=MODEL_CHOICES,
                value="",
                label=f"{prefix} model (blank = default for provider)",
                allow_custom_value=True,
                scale=2,
            )
        else:
            mdl = gr.Textbox(
                label=f"{prefix} model (blank = default)",
                placeholder=f"openai → {DEFAULT_MODELS['openai']} · ollama → {DEFAULT_MODELS['ollama']}",
                scale=2,
            )
    return prov, mdl


with gr.Blocks(title="ReviewMe — skill-aware code reviewer") as demo:
    gr.Markdown("# ReviewMe — skill-aware code reviewer")
    gr.Markdown(
        "Pick **OpenAI** (cloud) or **Ollama** (local) per tab. "
        "Defaults: OpenAI `gpt-5.4-mini`, Ollama `qwen3:8b`. "
        f"Skill packs: **{', '.join(s['name'] for s in skills.list_skills())}**"
    )

    with gr.Tabs():
        # ── Tab 1 ───────────────────────────────────────────
        with gr.Tab("Ask question"):
            t1_provider, t1_model = _provider_row("Tab 1")
            gr.ChatInterface(
                fn=chat_fn,
                additional_inputs=[t1_provider, t1_model],
                description="Paste code. Model reviews with skill access. Tool calls render inline.",
                examples=EXAMPLES,
                cache_examples=False,
            )

        # ── Tab 2 ───────────────────────────────────────────
        with gr.Tab("Review my project"):
            gr.Markdown(
                "Paste a public GitHub URL. The tool detects the stack (no AI), "
                "then sends key files to the selected model for review with version-aware skill use."
            )
            t2_provider, t2_model = _provider_row("Tab 2")
            with gr.Row():
                url_in     = gr.Textbox(
                    label="GitHub URL or `owner/repo`",
                    placeholder="https://github.com/owner/repo  or  https://github.com/owner/repo/tree/branch/subdir",
                    scale=4,
                )
                review_btn = gr.Button("Detect & Review", variant="primary", scale=1)
            review_out = gr.Markdown(label="Output")
            review_btn.click(
                review_project_stream,
                inputs=[url_in, t2_provider, t2_model],
                outputs=review_out,
            )
            gr.Examples(
                examples=[
                    ["https://github.com/vercel/next.js/tree/canary/examples/blog-starter"],
                    ["https://github.com/gothinkster/angular-realworld-example-app"],
                    ["https://github.com/vercel/next.js/tree/v14.2.15/examples/blog-starter"],
                ],
                inputs=url_in,
            )

        # ── Tab 3 ───────────────────────────────────────────
        with gr.Tab("Full pipeline (Layers 0-4)"):
            gr.Markdown(
                "Runs Layer 0 (deps) + Layer 1 (static) + Layer 2 (AST) on disk, "
                "then Layer 3 (3 parallel AI calls: architecture, job-fit, teaching). "
                "If a JD is provided, also runs Layer 4 (project suggestion from JD + web research). "
                "**JD-only mode**: leave repo blank to run Layer 4 alone. "
                "Layer 3 **never decides severity** — Layer 1 is authoritative; AI only explains."
            )
            t3_provider, t3_model = _provider_row("Tab 3", model_as_dropdown=True)
            full_target = gr.Textbox(
                label="Local path OR GitHub URL (optional — leave blank for JD-only Layer 4)",
                placeholder=r"C:\ReviewMe   or   https://github.com/owner/repo   (or blank)",
            )
            full_jd = gr.Textbox(
                label="Job Description (optional with repo · required for JD-only mode)",
                placeholder="Paste the JD here. Leave blank to skip Layer 4; paste alone for JD-only Layer 4.",
                lines=6,
            )
            full_btn = gr.Button("Run full pipeline", variant="primary")
            full_out = gr.Markdown(label="Output")
            full_btn.click(
                full_review_stream,
                inputs=[full_target, full_jd, t3_provider, t3_model],
                outputs=full_out,
            )


if __name__ == "__main__":
    demo.launch()
