FROM python:3.12-slim

RUN pip install --no-cache-dir \
    fastapi==0.109.0 \
    uvicorn==0.27.0 \
    httpx==0.26.0 \
    pandas==2.2.0 \
    numpy==1.26.4 \
    scikit-learn==1.4.0 \
    pydantic==2.6.0 \
    pydantic-settings==2.1.0 \
    aiohttp==3.9.3 \
    python-dateutil==2.8.2

WORKDIR /app
COPY . .

EXPOSE 8001

CMD ["python3", "main.py"]
