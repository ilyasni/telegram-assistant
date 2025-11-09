FROM paddlepaddle/paddle:2.6.1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY services/paddleocr/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /app

EXPOSE 8008

CMD ["uvicorn", "services.paddleocr.app.server:app", "--host", "0.0.0.0", "--port", "8008", "--timeout-keep-alive", "120"]

