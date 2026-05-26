FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-prod.txt ./

RUN python -m pip install -r requirements-prod.txt

COPY googletrends_app ./googletrends_app

EXPOSE 8000

CMD ["uvicorn", "googletrends_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
