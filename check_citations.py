"""check_citations.py - STUDENT IMPLEMENTS `check`. Runs INSIDE the sandbox (standard library only).

research.py uploads this file to the sandbox and the lead agent runs it with the `execute` tool:
    python3 /tmp/work/research/check_citations.py [report.md] [sources.json]
It must exit 0 and print "OK: ..." when the report is consistent, else print each problem and exit 1.
"""
import json
import re
import sys

REPORT = "/tmp/work/report/report.md"
SOURCES = "/tmp/work/research/sources.json"


def check(report_text, sources):
    """Return a list of problem strings (empty list = OK)."""
    problems = []

    # Quy tắc 1 & 2: Kiểm tra cấu trúc mảng sources
    if not sources or not isinstance(sources, list):
        return ["no sources in sources.json"]

    source_map = {}
    seen_urls = {}

    for idx, item in enumerate(sources):
        if not isinstance(item, dict):
            problems.append(f"source item at index {idx} is not an object")
            continue

        n = item.get("n")
        # n phải là int (loại trừ bool vì bool kế thừa int trong Python)
        if type(n) is not int:
            problems.append(f"source item at index {idx} has non-integer n: {n}")
        elif n in source_map:
            problems.append(f"duplicate source number n={n} in sources.json")
        else:
            source_map[n] = item

        url = item.get("url")
        if not isinstance(url, str) or not (url.startswith("http://") or url.startswith("https://")):
            problems.append(f"source [{n}] has invalid url: {url}")
        else:
            if url in seen_urls:
                problems.append(f"duplicate url detected in sources: {url} (used in [{seen_urls[url]}] and [{n}])")
            else:
                seen_urls[url] = n

    # Quy tắc 3: Tách báo cáo tại tiêu đề "## References"
    ref_match = re.search(r"^##\s+References\s*$", report_text, re.MULTILINE)
    if not ref_match:
        problems.append("missing '## References' section")
        return problems

    body = report_text[:ref_match.start()]
    references_section = report_text[ref_match.end():]

    # Quy tắc 7: Làm sạch phần thân trước khi trích xuất [n]
    # Bỏ khối code ```...``` và inline code `...`
    clean_body = re.sub(r"```[\s\S]*?```", "", body)
    clean_body = re.sub(r"`[^`\n]*`", "", clean_body)
    # Bỏ markdown links dạng [text](url) để không nhầm [n](url) thành citation
    clean_body = re.sub(r"\[[^\]\n]*\]\([^\)\n]+\)", "", clean_body)

    # Trích xuất trích dẫn dạng [n], [1, 2], hoặc [1-3]
    cited = set()
    citation_brackets = re.findall(r"\[([0-9\s,\-]+)\]", clean_body)
    for group in citation_brackets:
        parts = group.split(",")
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                sub_parts = part.split("-")
                if len(sub_parts) == 2 and sub_parts[0].isdigit() and sub_parts[1].isdigit():
                    start_num, end_num = int(sub_parts[0]), int(sub_parts[1])
                    if start_num <= end_num:
                        cited.update(range(start_num, end_num + 1))
            elif part.isdigit():
                cited.add(int(part))

    # Quy tắc 4: Đối chiếu 2 chiều giữa trích dẫn thân bài và sources.json
    for c in sorted(cited):
        if c not in source_map:
            problems.append(f"[{c}] cited but missing from sources.json")

    for s_num in sorted(source_map.keys()):
        if s_num not in cited:
            problems.append(f"source [{s_num}] never cited")

    # Quy tắc 5 & 6: Kiểm tra chi tiết từng dòng trong ## References
    ref_lines = [line.strip() for line in references_section.splitlines() if line.strip()]
    seen_ref_numbers = set()

    url_regex = re.compile(r"https?://[^\s<>\"')\]]+")

    for line in ref_lines:
        line_match = re.match(r"^\[(\d+)\]\s*(.*)$", line)
        if not line_match:
            continue

        ref_n = int(line_match.group(1))
        line_content = line_match.group(2)

        if ref_n in seen_ref_numbers:
            problems.append(f"duplicate reference entry for [{ref_n}] in References section")
        seen_ref_numbers.add(ref_n)

        if ref_n not in source_map:
            problems.append(f"reference [{ref_n}] in References section does not exist in sources.json")
            continue

        # Tìm các URL trong dòng tham khảo
        urls = url_regex.findall(line_content)
        # Làm sạch dấu chấm hoặc dấu phẩy vô tình bám ở cuối URL
        cleaned_urls = [u.rstrip(".,;") for u in urls]

        if len(cleaned_urls) != 1:
            problems.append(
                f"reference line for [{ref_n}] must contain exactly one URL, found {len(cleaned_urls)}: '{line}'"
            )
        else:
            expected_url = source_map[ref_n].get("url")
            if cleaned_urls[0] != expected_url:
                problems.append(
                    f"reference URL for [{ref_n}] mismatch: expected '{expected_url}', got '{cleaned_urls[0]}'"
                )

    # Đảm bảo mọi nguồn đều có đúng một dòng trong References
    for s_num in sorted(source_map.keys()):
        if s_num not in seen_ref_numbers:
            problems.append(f"missing reference line for source [{s_num}] in References section")

    return problems


def main(argv):
    report_path = argv[1] if len(argv) > 1 else REPORT
    sources_path = argv[2] if len(argv) > 2 else SOURCES
    try:
        with open(report_path, encoding="utf-8") as f:
            report = f.read()
        with open(sources_path, encoding="utf-8") as f:
            sources = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"cannot read inputs: {exc}")
        return 1
    problems = check(report, sources)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"OK: {len(sources)} sources, all citations resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))