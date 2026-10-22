# 知言 AI - Docker 部署完整方案

## 📦 Docker 架构说明

### 当前项目 Docker 使用情况

```
现有文件：backend/coding-sandbox/Dockerfile
用途：旧 ZIP 工作台的代码执行沙箱（已下线）
状态：不再使用，可以忽略
```

### 推荐的 Docker 架构

```
知言 AI 完整部署
├── docker-compose.yml          # 一键启动所有服务
├── Dockerfile                  # 后端容器
├── frontend.Dockerfile         # 前端容器（可选）
├── nginx.conf                  # Nginx 配置
└── .env                        # 环境变量
```

---

## 🚀 方案一：Docker Compose 一键部署（推荐）

### 1. 创建 docker-compose.yml

这是最简单的方式，一个命令启动所有服务：

```yaml
version: '3.8'

services:
  # 后端 FastAPI 服务
  backend:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: zhiyan-backend
    ports:
      - "8000:8000"
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - QWEN_MODEL=${QWEN_MODEL:-qwen-plus}
      - AUTH_DB_PATH=/app/data/zhiyan.sqlite3
      - SESSION_COOKIE_SECURE=false  # 本地开发用 false，生产环境用 true
    volumes:
      - ./backend:/app
      - ./backend/data:/app/data  # 持久化数据库
      - ./backend/uploads:/app/uploads  # 持久化附件
    networks:
      - zhiyan-network
    restart: unless-stopped
    command: uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

  # 前端 Vue 服务（开发环境）
  frontend-dev:
    image: node:20-alpine
    container_name: zhiyan-frontend-dev
    working_dir: /app
    ports:
      - "5173:5173"
    volumes:
      - ./frontend:/app
      - /app/node_modules  # 匿名卷，避免本机 node_modules 冲突
    environment:
      - NODE_ENV=development
    networks:
      - zhiyan-network
    command: sh -c "pnpm install && pnpm dev --host 0.0.0.0"
    depends_on:
      - backend

  # Nginx 反向代理（生产环境）
  nginx:
    image: nginx:alpine
    container_name: zhiyan-nginx
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./frontend/dist:/usr/share/nginx/html:ro  # 前端构建产物
      - ./ssl:/etc/nginx/ssl:ro  # SSL 证书
    networks:
      - zhiyan-network
    depends_on:
      - backend
    restart: unless-stopped

  # Qdrant 向量数据库（可选，用于 RAG）
  qdrant:
    image: qdrant/qdrant:latest
    container_name: zhiyan-qdrant
    ports:
      - "6333:6333"
    volumes:
      - qdrant_storage:/qdrant/storage
    networks:
      - zhiyan-network
    restart: unless-stopped

networks:
  zhiyan-network:
    driver: bridge

volumes:
  qdrant_storage:
```

### 2. 创建后端 Dockerfile

```dockerfile
FROM python:3.12-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY backend/requirements.txt .

# 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY backend /app

# 创建数据目录
RUN mkdir -p /app/data /app/uploads

# 暴露端口
EXPOSE 8000

# 启动命令（生产环境）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### 3. 创建前端构建 Dockerfile（生产环境）

```dockerfile
# 构建阶段
FROM node:20-alpine AS builder

WORKDIR /app

# 安装 pnpm
RUN npm install -g pnpm

# 复制依赖文件
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# 安装依赖
RUN pnpm install --frozen-lockfile

# 复制源代码
COPY frontend .

# 构建
RUN pnpm build

# 运行阶段
FROM nginx:alpine

# 复制构建产物到 Nginx
COPY --from=builder /app/dist /usr/share/nginx/html

# 复制 Nginx 配置
COPY nginx.conf /etc/nginx/nginx.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
```

### 4. 创建 Nginx 配置

```nginx
events {
    worker_connections 1024;
}

http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;

    # 日志
    access_log /var/log/nginx/access.log;
    error_log /var/log/nginx/error.log;

    # 上传大小限制
    client_max_body_size 10M;

    # Gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml;

    # 后端服务
    upstream backend {
        server backend:8000;
    }

    # HTTP 重定向到 HTTPS（生产环境）
    server {
        listen 80;
        server_name your-domain.com;
        return 301 https://$server_name$request_uri;
    }

    # HTTPS 服务器
    server {
        listen 443 ssl http2;
        server_name your-domain.com;

        # SSL 证书（生产环境需要配置）
        ssl_certificate /etc/nginx/ssl/fullchain.pem;
        ssl_certificate_key /etc/nginx/ssl/privkey.pem;
        ssl_protocols TLSv1.2 TLSv1.3;
        ssl_ciphers HIGH:!aNULL:!MD5;

        # 前端静态文件
        location / {
            root /usr/share/nginx/html;
            try_files $uri $uri/ /index.html;
            add_header Cache-Control "no-cache";
        }

        # 后端 API 代理
        location /api/ {
            proxy_pass http://backend;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            
            # WebSocket 支持（如果需要）
            proxy_http_version 1.1;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection "upgrade";
            
            # 超时设置
            proxy_connect_timeout 60s;
            proxy_send_timeout 60s;
            proxy_read_timeout 120s;
        }

        # 文档页面
        location /docs {
            proxy_pass http://backend;
            proxy_set_header Host $host;
        }
    }

    # HTTP 服务器（开发环境）
    server {
        listen 80;
        server_name localhost 127.0.0.1;

        location / {
            root /usr/share/nginx/html;
            try_files $uri $uri/ /index.html;
        }

        location /api/ {
            proxy_pass http://backend:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }
}
```

### 5. 环境变量配置

创建 `.env` 文件（不要提交到 Git）：

```bash
# 模型配置
DASHSCOPE_API_KEY=your_api_key_here
QWEN_MODEL=qwen-plus
QWEN_VISION_MODEL=qwen-vl-plus
QWEN_CODING_MODEL=qwen-coder-plus

# 数据库
AUTH_DB_PATH=/app/data/zhiyan.sqlite3

# 会话配置
SESSION_MAX_AGE=604800
SESSION_COOKIE_SECURE=true

# SMTP 邮件
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_USERNAME=your_email@example.com
SMTP_PASSWORD=your_password
SMTP_FROM=noreply@your-domain.com
EMAIL_CODE_SECRET=your_secret_key_here

# 加密密钥
MODEL_CONFIG_SECRET=your_encryption_key_here

# CORS
CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com

# RAG
RAG_ENABLED=true
QDRANT_URL=http://qdrant:6333
EMBEDDING_MODEL=text-embedding-v3
```

---

## 🎯 使用方法

### 开发环境启动

```bash
# 1. 确保已安装 Docker 和 Docker Compose
docker --version
docker-compose --version

# 2. 启动所有服务
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 访问应用
# 前端: http://localhost:5173
# 后端: http://localhost:8000
# API 文档: http://localhost:8000/docs

# 5. 停止服务
docker-compose down

# 6. 重新构建
docker-compose up -d --build
```

### 生产环境部署

```bash
# 1. 构建前端
cd frontend
pnpm install
pnpm build

# 2. 准备 SSL 证书（Let's Encrypt）
sudo certbot certonly --standalone -d your-domain.com

# 3. 复制证书到项目
mkdir -p ssl
sudo cp /etc/letsencrypt/live/your-domain.com/fullchain.pem ssl/
sudo cp /etc/letsencrypt/live/your-domain.com/privkey.pem ssl/

# 4. 启动生产环境
docker-compose -f docker-compose.prod.yml up -d

# 5. 查看状态
docker-compose ps
```

---

## 🔧 方案二：单独使用 Docker（更灵活）

### 只 Docker 化后端

```bash
# 1. 构建镜像
docker build -t zhiyan-backend -f Dockerfile .

# 2. 运行容器
docker run -d \
  --name zhiyan-backend \
  -p 8000:8000 \
  -e DASHSCOPE_API_KEY=your_key \
  -v $(pwd)/backend/data:/app/data \
  -v $(pwd)/backend/uploads:/app/uploads \
  zhiyan-backend

# 3. 查看日志
docker logs -f zhiyan-backend

# 4. 进入容器
docker exec -it zhiyan-backend bash

# 5. 停止容器
docker stop zhiyan-backend
docker rm zhiyan-backend
```

### 只 Docker 化前端

```bash
# 1. 构建镜像
docker build -t zhiyan-frontend -f frontend.Dockerfile .

# 2. 运行容器
docker run -d \
  --name zhiyan-frontend \
  -p 80:80 \
  zhiyan-frontend
```

---

## 📋 常见问题

### Q1: Docker 和本地开发有什么区别？

```
本地开发：
  ✓ 修改代码立即生效（热重载）
  ✓ 调试方便
  ✗ 环境配置复杂
  ✗ 团队环境不一致

Docker 开发：
  ✓ 环境一致
  ✓ 一键启动
  ✓ 易于部署
  ✗ 文件变更有延迟（可用卷挂载解决）
```

### Q2: 什么时候用 Docker？

```
推荐使用：
✓ 生产环境部署
✓ 团队协作（环境一致）
✓ 需要多服务（后端+前端+数据库）
✓ CI/CD 自动化

不推荐：
✗ 个人开发初期
✗ 频繁修改代码调试
```

### Q3: 端口配置

```yaml
# docker-compose.yml 中的端口映射
ports:
  - "宿主机端口:容器端口"

例如：
  - "8000:8000"   # 本机 8000 映射到容器 8000
  - "5173:5173"   # 前端开发服务器
  - "80:80"       # HTTP
  - "443:443"     # HTTPS
```

### Q4: 数据持久化

```yaml
# 使用 volumes 持久化数据
volumes:
  - ./backend/data:/app/data           # 数据库文件
  - ./backend/uploads:/app/uploads     # 上传的附件
  - qdrant_storage:/qdrant/storage    # 向量数据库

# 注意：Docker 容器删除后，volume 数据不会丢失
```

### Q5: 开发时修改代码怎么办？

```yaml
# 方案 1: 挂载代码目录（推荐开发环境）
volumes:
  - ./backend:/app

# 方案 2: 重新构建镜像（生产环境）
docker-compose up -d --build

# 方案 3: 热重载（FastAPI）
command: uvicorn app.main:app --reload --host 0.0.0.0
```

---

## 🎓 Docker 基础知识

### Docker vs Docker Compose

```
Docker:
  单个容器的构建和运行
  docker build
  docker run

Docker Compose:
  多个容器的编排
  一个 YAML 文件定义所有服务
  docker-compose up
```

### 核心概念

```
镜像 (Image):
  相当于"安装包"
  包含应用和依赖

容器 (Container):
  相当于"进程"
  镜像的运行实例

卷 (Volume):
  数据持久化
  容器删除后数据不丢失

网络 (Network):
  容器间通信
  同一网络内可用服务名访问
```

---

## ✅ 推荐的部署流程

### 第一次部署

```bash
# 1. 克隆项目
git clone your-repo
cd zhiyan-ai

# 2. 创建环境变量文件
cp .env.example .env
# 编辑 .env 填入真实配置

# 3. 创建 Docker 配置文件
# 复制上面的 docker-compose.yml
# 复制 Dockerfile

# 4. 启动服务
docker-compose up -d

# 5. 初始化数据库
docker-compose exec backend python backend/init_db.py

# 6. 查看日志
docker-compose logs -f

# 7. 访问测试
curl http://localhost:8000/api/health
```

### 更新代码

```bash
# 1. 拉取最新代码
git pull

# 2. 重新构建并启动
docker-compose up -d --build

# 3. 如果数据库结构变更
docker-compose exec backend python backend/migrate_db.py --backup
```

---

## 📊 性能优化

### 多阶段构建（减小镜像体积）

```dockerfile
# 构建阶段
FROM python:3.12 AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# 运行阶段
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY backend .
ENV PATH=/root/.local/bin:$PATH
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0"]
```

### Docker Compose 生产配置

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  backend:
    image: your-registry/zhiyan-backend:latest
    restart: always
    environment:
      - SESSION_COOKIE_SECURE=true
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
```

---

## 🚀 总结

**你的问题回答：**

> 是直接创建一个相关的文件然后再在里面配置好相关的端口就好了是吗？

**答案：是的，但需要这几步：**

1. **创建 `Dockerfile`** - 定义如何构建镜像
2. **创建 `docker-compose.yml`** - 配置所有服务和端口
3. **配置 `.env`** - 环境变量
4. **运行 `docker-compose up`** - 一键启动

**端口配置示例：**
```yaml
ports:
  - "8000:8000"  # 后端
  - "5173:5173"  # 前端开发
  - "80:80"      # Nginx HTTP
  - "443:443"    # Nginx HTTPS
```

**推荐做法：**
- 开发阶段：本地直接运行（不用 Docker）
- 生产部署：用 Docker Compose 一键部署
- 团队协作：用 Docker 统一环境

需要我帮你创建具体的配置文件吗？
