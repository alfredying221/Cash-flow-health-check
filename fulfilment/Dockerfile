FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY fulfilment/requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY fulfilment ./fulfilment
COPY senalo_analysis ./senalo_analysis

USER appuser

EXPOSE 8080

CMD ["uvicorn", "fulfilment.app:app", "--host", "0.0.0.0", "--port", "8080"]
