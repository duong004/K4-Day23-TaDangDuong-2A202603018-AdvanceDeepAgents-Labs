"""tools.py - STUDENT IMPLEMENTS.  Source tools for the research agents.   Guide: GUIDE.md, part 1.

Rules for every tool:
  * runs on the HOST (not in the sandbox): API keys must never enter the sandbox;
  * returns a STRING (JSON text of compact records) and NEVER raises:
        "NO RESULTS"  when the source answers with nothing,
        "ERROR: ..."  when the source keeps failing after the retries (the agent then tries another source);
  * the docstring is the tool description the LLM reads: keep it precise (what it does, what it returns, when to use it).
Try your tools without any agent:   python tools.py
"""
import json
import os
import random
import re
import time
import xml.etree.ElementTree as ET

import httpx
from langchain_core.tools import tool

# ---- constants (given) ----
ARXIV_URL = "https://export.arxiv.org/api/query"  # https only: http answers 301
HF_DAILY_URL = "https://huggingface.co/api/daily_papers"
HF_SEARCH_URL = "https://huggingface.co/api/papers/search"
EXA_URL = "https://mcp.exa.ai/mcp"

# Module-level tracking for arXiv etiquette (>= 3s between calls)
_LAST_ARXIV_CALL = 0.0


class RetryableError(Exception):
    """Given. Raise it inside a call to ask with_retry to wait and try again (retry_after in seconds, optional)."""

    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


# ---- Helper: Redact secret keys from strings ----
def _redact_secret(text: str) -> str:
    if not text:
        return ""
    key = os.getenv("EXA_API_KEY", "").strip()
    if key and key in text:
        text = text.replace(key, "[REDACTED]")
    # Redact query parameter patterns if present
    text = re.sub(r"exaApiKey=[^&\s'\"]+", "exaApiKey=[REDACTED]", text)
    return text


# ---- TODO 1: retry helper ----
def with_retry(fn, *, attempts=5, base=1.0, cap=30.0):
    """Call fn(); when it raises RetryableError or temporary network/HTTP errors, wait and call it again."""
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:
            retryable = False
            retry_after = None

            if isinstance(exc, RetryableError):
                retryable = True
                retry_after = exc.retry_after
            elif isinstance(exc, httpx.HTTPStatusError):
                if exc.response.status_code in {429, 500, 502, 503, 504}:
                    retryable = True
                    header_val = exc.response.headers.get("Retry-After")
                    if header_val:
                        try:
                            retry_after = float(header_val)
                        except (ValueError, TypeError):
                            pass
            elif isinstance(exc, httpx.TransportError):
                retryable = True

            # Lỗi không thể retry: ném ra ngay lập tức
            if not retryable:
                raise exc

            # Lần thử cuối cùng: ném ra lỗi, không ngủ thêm
            if attempt == attempts - 1:
                raise exc

            # Tính toán thời gian delay
            if retry_after is not None:
                delay = float(retry_after)
            else:
                backoff = base * (2**attempt)
                jitter = random.uniform(0.0, 0.5 * backoff)
                delay = backoff + jitter

            delay = min(delay, cap)
            time.sleep(delay)


# ---- Helper for arXiv rate limiting ----
def _wait_arxiv_etiquette():
    global _LAST_ARXIV_CALL
    elapsed = time.time() - _LAST_ARXIV_CALL
    if elapsed < 3.0:
        time.sleep(3.0 - elapsed)
    _LAST_ARXIV_CALL = time.time()


# ---- TODO 2: arXiv ----
@tool
def arxiv_search(query: str, max_results: int = 10) -> str:
    """Search arXiv papers by keywords, newest first. Returns a JSON list of {id, url, published, title, summary}."""
    try:
        # Làm sạch query: chỉ giữ các từ/ký tự an toàn
        terms = re.findall(r"[A-Za-z0-9_-]+", query)
        terms = [t for t in terms if t.strip()]
        if not terms:
            return "NO RESULTS"

        search_query = " AND ".join(f"all:{t}" for t in terms)
        max_results_clamped = max(1, min(int(max_results), 30))

        params = {
            "search_query": search_query,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": max_results_clamped,
        }

        def _do_request():
            _wait_arxiv_etiquette()
            with httpx.Client(timeout=25.0) as client:
                res = client.get(ARXIV_URL, params=params)
                res.raise_for_status()
                return res.text

        # arXiv có thể gặp 429 khi dùng chung IP trường, đặt cap 60s
        xml_text = with_retry(_do_request, attempts=5, base=2.0, cap=60.0)

        # Parse Atom XML
        root = ET.fromstring(xml_text)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        entries = root.findall("atom:entry", namespace)
        if not entries:
            return "NO RESULTS"

        records = []
        for entry in entries:
            id_elem = entry.find("atom:id", namespace)
            title_elem = entry.find("atom:title", namespace)
            summary_elem = entry.find("atom:summary", namespace)
            published_elem = entry.find("atom:published", namespace)

            if id_elem is None or not id_elem.text:
                continue

            raw_id = id_elem.text.strip().split("/abs/")[-1]
            clean_id = re.sub(r"v\d+$", "", raw_id)

            title = " ".join(title_elem.text.split()) if title_elem is not None and title_elem.text else ""
            summary = " ".join(summary_elem.text.split()) if summary_elem is not None and summary_elem.text else ""
            if len(summary) > 600:
                summary = summary[:600] + "..."

            published = published_elem.text.strip()[:10] if published_elem is not None and published_elem.text else ""

            records.append({
                "id": clean_id,
                "url": f"https://arxiv.org/abs/{clean_id}",
                "published": published,
                "title": title,
                "summary": summary,
            })

        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)

    except Exception as exc:
        return _redact_secret(f"ERROR: {type(exc).__name__}: {exc}")


# ---- TODO 3: Hugging Face ----
@tool
def hf_daily_papers(limit: int = 30, date: str = "", keyword: str = "") -> str:
    """Hugging Face Daily Papers = what is trending in AI research. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars} sorted by upvotes. `date` is YYYY-MM-DD (empty = latest).
    `keyword` filters title/summary; there is no topic search on this endpoint (use hf_search_papers for a topic)."""
    try:
        limit_clamped = max(1, min(int(limit), 100))
        params = {"limit": limit_clamped}
        if date and date.strip():
            params["date"] = date.strip()

        def _do_request():
            with httpx.Client(timeout=20.0) as client:
                res = client.get(HF_DAILY_URL, params=params)
                res.raise_for_status()
                return res.json()

        data = with_retry(_do_request, attempts=5, base=1.0, cap=30.0)
        if not isinstance(data, list) or not data:
            return "NO RESULTS"

        records = []
        for item in data:
            if not isinstance(item, dict):
                continue
            paper = item.get("paper")
            if not isinstance(paper, dict):
                continue
            pid = paper.get("id")
            if not pid:
                continue

            title = " ".join((paper.get("title") or item.get("title") or "").split())
            raw_summary = paper.get("summary") or item.get("summary") or ""
            summary = " ".join(raw_summary.split())
            if len(summary) > 600:
                summary = summary[:600] + "..."

            pub = paper.get("publishedAt") or item.get("publishedAt") or ""
            published = str(pub)[:10]

            upvotes = paper.get("upvotes", item.get("upvotes", 0))
            try:
                upvotes = int(upvotes or 0)
            except (ValueError, TypeError):
                upvotes = 0

            github = paper.get("githubRepo") or item.get("githubRepo") or ""
            stars = paper.get("githubStars") or item.get("githubStars") or 0
            try:
                stars = int(stars or 0)
            except (ValueError, TypeError):
                stars = 0

            records.append({
                "id": str(pid),
                "url": f"https://huggingface.co/papers/{pid}",
                "published": published,
                "title": title,
                "summary": summary,
                "upvotes": upvotes,
                "github": github,
                "stars": stars,
            })

        if keyword and keyword.strip():
            kw = keyword.strip().lower()
            records = [r for r in records if kw in r["title"].lower() or kw in r["summary"].lower()]

        records.sort(key=lambda r: r["upvotes"], reverse=True)

        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)

    except Exception as exc:
        return _redact_secret(f"ERROR: {type(exc).__name__}: {exc}")


@tool
def hf_search_papers(query: str, limit: int = 10) -> str:
    """Search Hugging Face papers by topic. Returns a JSON list of
    {id, url, published, title, summary, upvotes, github, stars}."""
    try:
        if not query or not query.strip():
            return "NO RESULTS"

        limit_clamped = max(1, min(int(limit), 50))
        params = {"q": query.strip(), "limit": limit_clamped}

        def _do_request():
            with httpx.Client(timeout=20.0) as client:
                res = client.get(HF_SEARCH_URL, params=params)
                res.raise_for_status()
                return res.json()

        data = with_retry(_do_request, attempts=5, base=1.0, cap=30.0)
        if not isinstance(data, list) or not data:
            return "NO RESULTS"

        records = []
        for item in data:
            if not isinstance(item, dict):
                continue
            paper = item.get("paper")
            if not isinstance(paper, dict):
                paper = item

            pid = paper.get("id")
            if not pid:
                continue

            title = " ".join((paper.get("title") or item.get("title") or "").split())
            # Ưu tiên ai_summary khi có
            raw_summary = paper.get("ai_summary") or item.get("ai_summary") or paper.get("summary") or item.get("summary") or ""
            summary = " ".join(raw_summary.split())
            if len(summary) > 600:
                summary = summary[:600] + "..."

            pub = paper.get("publishedAt") or item.get("publishedAt") or ""
            published = str(pub)[:10]

            upvotes = paper.get("upvotes", item.get("upvotes", 0))
            try:
                upvotes = int(upvotes or 0)
            except (ValueError, TypeError):
                upvotes = 0

            github = paper.get("githubRepo") or item.get("githubRepo") or ""
            stars = paper.get("githubStars") or item.get("githubStars") or 0
            try:
                stars = int(stars or 0)
            except (ValueError, TypeError):
                stars = 0

            records.append({
                "id": str(pid),
                "url": f"https://huggingface.co/papers/{pid}",
                "published": published,
                "title": title,
                "summary": summary,
                "upvotes": upvotes,
                "github": github,
                "stars": stars,
            })

        if not records:
            return "NO RESULTS"
        return json.dumps(records, ensure_ascii=False)

    except Exception as exc:
        return _redact_secret(f"ERROR: {type(exc).__name__}: {exc}")


# ---- Helper for Exa MCP JSON-RPC calls ----
def _call_exa_mcp(tool_name: str, arguments: dict) -> str:
    api_key = os.getenv("EXA_API_KEY", "").strip()
    endpoint = f"{EXA_URL}?exaApiKey={api_key}" if api_key else EXA_URL

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
        },
    }

    def _do_request():
        with httpx.Client(timeout=35.0) as client:
            res = client.post(endpoint, json=payload, headers=headers)
            res.raise_for_status()

            raw_text = res.text
            data_obj = None

            # Phân tích phản hồi dạng SSE hoặc plain JSON
            for line in raw_text.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    json_str = line[5:].strip()
                    if json_str:
                        try:
                            data_obj = json.loads(json_str)
                            break
                        except json.JSONDecodeError:
                            pass

            if data_obj is None:
                try:
                    data_obj = res.json()
                except Exception:
                    raise RuntimeError(f"Unparseable Exa response: {raw_text[:200]}")

            if "error" in data_obj:
                err = data_obj["error"]
                err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                if "rate limit" in err_msg.lower() or "429" in err_msg:
                    raise RetryableError(f"Exa rate limit: {err_msg}")
                raise RuntimeError(f"Exa RPC error: {err_msg}")

            result = data_obj.get("result", {})

            # Phát hiện cờ giới hạn tốc độ (Rate Limit) ở HTTP 200
            meta = result.get("_meta", {})
            if isinstance(meta, dict):
                for k, v in meta.items():
                    if "rate" in k.lower() and "limit" in k.lower():
                        raise RetryableError(f"Exa rate limit flag detected in _meta: {k}={v}")
                    if str(v).lower() in ["rate_limited", "ratelimited", "true"] and ("limit" in k.lower() or "rate" in k.lower()):
                        raise RetryableError(f"Exa rate limit flag detected in _meta: {k}={v}")

            # Trích xuất nội dung text
            content_list = result.get("content", [])
            text_parts = [
                item.get("text", "")
                for item in content_list
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            full_text = "\n".join(text_parts).strip()

            # Kiểm tra text cảnh báo rate limit trên bản free tier
            lower_text = full_text.lower()
            if "rate limit" in lower_text and ("exceeded" in lower_text or "tier" in lower_text or "requests" in lower_text):
                raise RetryableError(f"Exa rate limit text detected: {full_text[:120]}")

            return full_text

    return with_retry(_do_request, attempts=5, base=2.0, cap=60.0)


# ---- TODO 4: web search / fetch through the Exa MCP endpoint ----
@tool
def web_search(query: str, objective: str = "", num_results: int = 5) -> str:
    """Search the web (Exa). Describe the ideal page in natural language. Returns clean text of the top results with URLs."""
    try:
        if not query or not query.strip():
            return "NO RESULTS"

        obj = objective.strip() if objective and objective.strip() else f"Find authoritative research papers, surveys, and articles on {query}"
        num_res = max(1, min(int(num_results), 10))

        args = {
            "query": query.strip(),
            "objective": obj,
            "numResults": num_res,
        }
        res_text = _call_exa_mcp("web_search_exa", args)
        if not res_text:
            return "NO RESULTS"
        return res_text

    except Exception as exc:
        return _redact_secret(f"ERROR: {type(exc).__name__}: {exc}")


@tool
def web_fetch(url: str) -> str:
    """Read the full content of one web page (e.g. an arXiv abstract page) as markdown. Long pages are truncated."""
    try:
        if not url or not url.strip():
            return "NO RESULTS"

        args = {"urls": [url.strip()]}
        res_text = _call_exa_mcp("web_fetch_exa", args)
        if not res_text:
            return "NO RESULTS"

        if len(res_text) > 12000:
            res_text = res_text[:12000] + "\n...[truncated]"
        return res_text

    except Exception as exc:
        return _redact_secret(f"ERROR: {type(exc).__name__}: {exc}")


# ---- TODO 5: registry (the researcher subagent gets exactly these) ----
SOURCE_TOOLS = [arxiv_search, hf_daily_papers, hf_search_papers, web_search, web_fetch]


if __name__ == "__main__":
    for name, fn, args in [
        ("arxiv_search", arxiv_search, {"query": "world model", "max_results": 3}),
        ("hf_daily_papers", hf_daily_papers, {"limit": 20}),
        ("hf_search_papers", hf_search_papers, {"query": "world model", "limit": 3}),
        ("web_search", web_search, {"query": "survey paper on world models", "num_results": 2}),
        ("web_fetch", web_fetch, {"url": "https://arxiv.org/abs/1803.10122"}),
    ]:
        try:
            print(f"== {name}\n{fn.invoke(args)[:400]}\n")
        except NotImplementedError as exc:
            print(f"== {name}: not implemented yet ({exc})\n")