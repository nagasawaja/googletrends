FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "apscheduler>=3.10.4" \
        "fastapi>=0.110.0" \
        "jinja2>=3.1.3" \
        "python-multipart>=0.0.9" \
        "pytrends>=4.9.2" \
        "requests>=2.31.0" \
        "uvicorn[standard]>=0.27.0"

COPY googletrends_app ./googletrends_app

EXPOSE 8000

CMD ["uvicorn", "googletrends_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
