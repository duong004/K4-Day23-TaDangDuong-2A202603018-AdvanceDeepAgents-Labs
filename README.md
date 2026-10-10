# Deep Research Multi-Agent System

Hệ thống Deep Research đa tác tử tự động lập kế hoạch, thu thập tài liệu khoa học từ nhiều họ nguồn (arXiv, Hugging Face, Exa Web Search) và tổng hợp báo cáo khảo sát học thuật với trích dẫn được xác thực tự động trong môi trường Sandbox cô lập (Daytona / Docker).

## 1. Yêu cầu hệ thống và Cài đặt

- **Môi trường**: Python 3.11 trở lên.
- **Môi trường Sandbox**: Tài khoản Daytona API hoặc Docker Desktop cục bộ.

### Cài đặt môi trường ảo và thư viện

```bash
# Tạo và kích hoạt môi trường ảo
python -m venv .venv

# Trên Linux/macOS:
source .venv/bin/activate
# Trên Windows (PowerShell):
.venv\Scripts\Activate.ps1

# Cài đặt thư viện phụ thuộc
pip install -r requirements.txt

```

### Cấu hình biến môi trường (`.env`)

Sao chép `.env.example` thành `.env` và điền các khóa API cần thiết:

```ini
# LLM Provider (Hỗ trợ Tool Calling)
LAB_MODEL=openai:gpt-4o-mini
OPENAI_API_KEY=your_openai_api_key_here

# Sandbox Provider (Daytona hoặc Docker)
DAYTONA_API_KEY=your_daytona_api_key_here
# Hoặc chạy Docker cục bộ:
# SANDBOX=docker
# SANDBOX_IMAGE=python:3.12-slim

# Exa Search MCP Endpoint
EXA_API_KEY=your_exa_api_key_here

```

## 2. Cách chạy hệ thống

Để thực hiện nghiên cứu chuyên sâu về một chủ đề bất kỳ, chạy lệnh:

```bash
python research.py "<chủ đề nghiên cứu>"

```

Ví dụ:

```bash
python research.py "survey about world model"

```

Hệ thống sẽ tự động:

1. Mở sandbox Daytona cô lập.
2. Tải các bộ kiểm tra và hoàn thiện trích dẫn (`check_citations.py`, `finalize_citations.py`) vào sandbox.
3. Lead Agent phân rã chủ đề thành các câu hỏi con độc lập bằng `write_todos`.
4. Gọi các subagent `researcher` song song thu thập tài liệu từ arXiv, Hugging Face và Exa Web.
5. Tổng hợp báo cáo học thuật theo `REPORT_TEMPLATE.md`.
6. Hoàn thiện và đối soát trích dẫn tự động bên trong sandbox.
7. Tải và lưu kết quả vào thư mục `reports/`.

## 3. Cấu trúc thư mục `reports/`

Kết quả của mỗi chủ đề gồm 3 tệp tin tương ứng:

* `<slug>.md`: Báo cáo khảo sát học thuật hoàn chỉnh (TL;DR, Background, Phân tích so sánh theo chủ đề, Xu hướng & Vấn đề mở, và danh mục `## References` được đánh số tự động).
* `<slug>.sources.json`: Danh mục tài liệu tham khảo chuẩn hóa (gồm `n`, `id`, `url`, `title`, `date`, `source`).
* `<slug>.meta.json`: Bằng chứng đo lường thực thi phục vụ chấm điểm (`model`, `elapsed_s`, `subagent_calls`, `tool_calls`, `tokens`, `n_sources`, `source_families`).

## 4. Tự kiểm tra kết quả (Self-Check)

Để kiểm tra tính hợp lệ của toàn bộ báo cáo và tính an toàn bảo mật trước khi nộp:

```bash
python self_check.py

```
