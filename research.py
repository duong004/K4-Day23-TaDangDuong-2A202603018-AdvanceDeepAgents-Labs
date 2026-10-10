"""research.py - STUDENT IMPLEMENTS.  The main script.   Guide: GUIDE.md, part 3.

Usage:  python research.py "survey about world model"
Result: reports/<slug>.md   reports/<slug>.sources.json   reports/<slug>.meta.json
"""
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

from agents import FINALIZER_PATH, REPORT_PATH, SOURCES_PATH, VALIDATOR_PATH, WORKDIR, build_lead_agent
from model import make_model
from sandbox import download, open_sandbox, upload

ROOT = Path(__file__).parent
REPORTS = ROOT / "reports"
VALIDATOR_SOURCE = ROOT / "check_citations.py"
FINALIZER_SOURCE = ROOT / "finalize_citations.py"   # provided: uploaded next to your validator


# ---- TODO 1: slugify ----
def slugify(topic: str) -> str:
    """Turn a topic into a safe file name: lower case, runs of non-word characters become one "-", max 60 chars,
    never empty (fall back to "topic"). The topic is user input: "../../x" must not escape reports/."""
    if not topic or not isinstance(topic, str):
        return "topic"
    # Thay thế toàn bộ ký tự không phải chữ/số bằng dấu gạch ngang
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", topic.lower()).strip("-")
    slug = slug[:60].rstrip("-")
    return slug if slug else "topic"


# ---- TODO 2: build_prompt ----
def build_prompt(topic: str) -> str:
    """The user message sent to the lead agent."""
    return (
        f"Conduct a comprehensive, multi-source deep research survey on the topic: '{topic}'.\n\n"
        "Strict Workflow Requirements:\n"
        "1. Plan your strategy with `write_todos`, decomposing this topic into at least 3 to 4 distinct sub-questions.\n"
        "2. Delegate each sub-question to the `researcher` subagent via the `task` tool with full context (topic, question, notes path, required source diversity).\n"
        "3. Ensure the retrieved literature covers at least 3 distinct source families (arxiv, hf-daily, hf-search, web) and consolidate all findings into /tmp/work/research/sources.json.\n"
        "4. Synthesize the findings into /tmp/work/report/report.md following REPORT_TEMPLATE.md (TL;DR, Background, thematic comparative analyses, Trends and open problems). Do NOT write ## References manually.\n"
        "5. Execute /tmp/work/research/finalize_citations.py inside the sandbox using `execute` to clean unused sources and generate ## References.\n"
        "6. Execute /tmp/work/research/check_citations.py inside the sandbox using `execute` until it prints 'OK'. Fix any citation discrepancy if reported.\n"
        "7. Call the `citation-checker` subagent via `task` to spot-check key factual claims against source URLs."
    )


# ---- TODO 3: summarize ----
def summarize(messages, elapsed, model_name):
    """Return {"model", "elapsed_s", "subagent_calls", "tool_calls": {name: count}, "tokens": {"input", "output"}}."""
    tool_counts = Counter()
    input_tokens = 0
    output_tokens = 0

    for msg in messages:
        # 1. Trích xuất tool calls
        calls = []
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            calls = msg.tool_calls
        elif isinstance(msg, dict) and msg.get("tool_calls"):
            calls = msg.get("tool_calls")
        elif hasattr(msg, "additional_kwargs"):
            calls = msg.additional_kwargs.get("tool_calls", [])

        for call in calls:
            name = None
            if isinstance(call, dict):
                name = call.get("name")
                if not name and "function" in call and isinstance(call["function"], dict):
                    name = call["function"].get("name")
            elif hasattr(call, "name"):
                name = call.name
            if name:
                tool_counts[name] += 1

        # 2. Trích xuất token usage từ metadata của Lead
        usage = None
        if hasattr(msg, "usage_metadata") and msg.usage_metadata:
            usage = msg.usage_metadata
        elif isinstance(msg, dict) and msg.get("usage_metadata"):
            usage = msg.get("usage_metadata")
        elif hasattr(msg, "response_metadata") and isinstance(msg.response_metadata, dict):
            usage = msg.response_metadata.get("token_usage") or msg.response_metadata.get("usage")

        if isinstance(usage, dict):
            in_t = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
            out_t = usage.get("output_tokens") or usage.get("completion_tokens") or 0
            try:
                input_tokens += int(in_t)
            except (ValueError, TypeError):
                pass
            try:
                output_tokens += int(out_t)
            except (ValueError, TypeError):
                pass

    subagent_calls = tool_counts.get("task", 0)

    return {
        "model": str(model_name),
        "elapsed_s": round(float(elapsed), 1),
        "subagent_calls": subagent_calls,
        "tool_calls": dict(tool_counts),
        "tokens": {
            "input": input_tokens,
            "output": output_tokens,
        },
    }


# ---- TODO 4: save_outputs ----
def save_outputs(backend, topic, messages, elapsed, model_name, reports_dir=REPORTS):
    """Download the report from the sandbox and write the three files into reports_dir. Return the report path."""
    reports_dir = Path(reports_dir)
    files = download(backend, [REPORT_PATH, SOURCES_PATH])

    report_bytes = files.get(REPORT_PATH)
    if not report_bytes:
        raise RuntimeError(f"Report file {REPORT_PATH} is missing from sandbox")

    report_text = report_bytes.decode("utf-8", errors="replace")
    if not report_text.strip():
        raise RuntimeError(f"Report file {REPORT_PATH} is empty")

    sources_bytes = files.get(SOURCES_PATH)
    if not sources_bytes:
        raise RuntimeError(f"Sources file {SOURCES_PATH} is missing from sandbox")

    try:
        sources = json.loads(sources_bytes.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"sources.json is invalid JSON: {exc}")

    if not isinstance(sources, list) or len(sources) == 0:
        raise RuntimeError("sources.json is empty or not a list")

    reports_dir.mkdir(parents=True, exist_ok=True)
    slug = slugify(topic)

    # Lấy danh sách các họ nguồn đã được chuẩn hóa và sắp xếp
    source_families = sorted(
        list({s.get("source") for s in sources if isinstance(s, dict) and s.get("source")})
    )

    summary = summarize(messages, elapsed, model_name)
    meta = {
        "topic": topic,
        **summary,
        "n_sources": len(sources),
        "source_families": source_families,
    }

    report_file = reports_dir / f"{slug}.md"
    sources_file = reports_dir / f"{slug}.sources.json"
    meta_file = reports_dir / f"{slug}.meta.json"

    # Chỉ ghi file khi mọi dữ liệu đều đã hợp lệ
    report_file.write_text(report_text, encoding="utf-8")
    sources_file.write_text(json.dumps(sources, indent=2, ensure_ascii=False), encoding="utf-8")
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return report_file


# ---- TODO 5: main ----
def main(topic):
    """Return the process exit code (0 ok, 1 failed run, 2 no topic)."""
    topic = topic.strip() if topic else ""
    if not topic:
        sys.stderr.write("Usage: python research.py <topic>\n")
        return 2

    start = time.monotonic()

    try:
        model = make_model()
    except Exception as exc:
        sys.stderr.write(f"FAILED: error initializing model: {exc}\n")
        return 1

    model_name = (
        getattr(model, "model_name", None)
        or getattr(model, "model", None)
        or os.getenv("LAB_MODEL", "unknown")
    )

    try:
        with open_sandbox() as backend:
            backend.execute(f"mkdir -p {WORKDIR}/research/notes {WORKDIR}/report")
            upload(
                backend,
                {
                    VALIDATOR_PATH: VALIDATOR_SOURCE.read_bytes(),
                    FINALIZER_PATH: FINALIZER_SOURCE.read_bytes(),
                },
            )
            agent = build_lead_agent(backend, model)
            prompt = build_prompt(topic)
            result = agent.invoke(
                {"messages": [{"role": "user", "content": prompt}]},
                config={"recursion_limit": 1000},
            )

            # Safeguard tất định trong sandbox (GUIDE 2.6):
            # Đảm bảo finalize_citations.py được chạy trong sandbox trước khi download
            backend.execute(f"python3 {FINALIZER_PATH}")
            val_res = backend.execute(f"python3 {VALIDATOR_PATH}")
            exit_code = getattr(val_res, "exit_code", 0)
            if exit_code != 0:
                stdout_text = getattr(val_res, "stdout", "") or getattr(val_res, "output", "")
                stderr_text = getattr(val_res, "stderr", "")
                output = f"{stdout_text}\n{stderr_text}".strip() or str(val_res)
                sys.stderr.write(f"SANDBOX VALIDATOR WARNING:\n{output}\n")

            elapsed = time.monotonic() - start
            messages = result.get("messages", []) if isinstance(result, dict) else []
            saved_report = save_outputs(backend, topic, messages, elapsed, model_name)
            print(f"Report saved to: {saved_report}")
            return 0
    except RuntimeError as exc:
        sys.stderr.write(f"FAILED: {exc}\n")
        return 1
    except Exception as exc:
        sys.stderr.write(f"FAILED: unexpected execution error: {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main(" ".join(sys.argv[1:])))