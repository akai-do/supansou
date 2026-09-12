FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 容器内必须监听所有接口才能被宿主机/其他容器访问
ENV HOST=0.0.0.0
EXPOSE 5000

CMD ["python", "app.py"]
