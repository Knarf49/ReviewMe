# ============================================================
# Gradio UI — two modes
#   Tab 1 "Ask question"      — chat code review with skill tools
#   Tab 2 "Review my project" — paste GitHub link → stack detect → AI review
# ============================================================
# Run:
#   .venv\Scripts\activate
#   python app.py
# Then open http://127.0.0.1:7860
# ============================================================

import json
import os
import re
import time
import traceback
from typing import Iterator

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

import skills
import detect_stack as ds

load_dotenv()

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

MODEL          = "gpt-5.4-mini"
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


def _tool_loop_stream(oa_messages: list[dict]) -> Iterator[tuple[str, dict | None, str]]:
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

    for step in range(MAX_TOOL_ITER):
        kwargs = dict(
            model=MODEL,
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

def chat_fn(user_msg: str, history: list[dict]) -> Iterator[list[dict]]:
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
        for kind, payload, text in _tool_loop_stream(oa_messages):
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


def review_project_stream(url: str) -> Iterator[str]:
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
        for kind, payload, text in _tool_loop_stream(oa_messages):
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

with gr.Blocks(title="ReviewMe — skill-aware code reviewer") as demo:
    gr.Markdown("# ReviewMe — skill-aware code reviewer")
    gr.Markdown(
        "Powered by `gpt-5.4-mini` with skill packs: "
        f"**{', '.join(s['name'] for s in skills.list_skills())}**"
    )

    with gr.Tabs():
        # ── Tab 1 ───────────────────────────────────────────
        with gr.Tab("Ask question"):
            gr.ChatInterface(
                fn=chat_fn,
                description="Paste code. Model reviews with skill access. Tool calls render inline.",
                examples=EXAMPLES,
                cache_examples=False,
            )

        # ── Tab 2 ───────────────────────────────────────────
        with gr.Tab("Review my project"):
            gr.Markdown(
                "Paste a public GitHub URL. The tool detects the stack (no AI), "
                "then sends key files to `gpt-5.4-mini` for review with version-aware skill use."
            )
            with gr.Row():
                url_in     = gr.Textbox(
                    label="GitHub URL or `owner/repo`",
                    placeholder="https://github.com/owner/repo  or  https://github.com/owner/repo/tree/branch/subdir",
                    scale=4,
                )
                review_btn = gr.Button("Detect & Review", variant="primary", scale=1)
            review_out = gr.Markdown(label="Output")
            review_btn.click(review_project_stream, inputs=url_in, outputs=review_out)
            gr.Examples(
                examples=[
                    ["https://github.com/vercel/next.js/tree/canary/examples/blog-starter"],
                    ["https://github.com/gothinkster/angular-realworld-example-app"],
                    ["https://github.com/vercel/next.js/tree/v14.2.15/examples/blog-starter"],
                ],
                inputs=url_in,
            )


if __name__ == "__main__":
    demo.launch()
