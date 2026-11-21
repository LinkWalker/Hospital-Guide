FROM python:3.11-slim

WORKDIR /app

# 先装依赖，利用 Docker 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码与前端（app/main.py 会在 /app/frontend 找页面）
COPY app ./app
COPY frontend ./frontend
COPY docs ./docs
COPY scripts ./scripts
COPY .env.example ./.env

# 运行时数据目录：SQLite（会话历史/知识库）需要可写
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# 暴露端口
EXPOSE 8000

# 启动服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
