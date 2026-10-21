FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 网盘加速依赖 aria2c（服务端多线程下载引擎）
RUN apt-get update \
    && apt-get install -y --no-install-recommends aria2 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY . .

# 容器内必须监听所有接口才能被宿主机/其他容器访问
ENV HOST=0.0.0.0
EXPOSE 5000

CMD ["python", "app.py"]
