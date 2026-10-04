FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir "pymysql>=1.1" "azure-identity>=1.17" "azure-storage-blob>=12.20"
COPY load_orders.py .
RUN useradd -m loader
USER loader
CMD ["python", "load_orders.py"]
