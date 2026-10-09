FROM python:3.12-slim

# 国内服务器（阿里云）直连 PyPI 很慢，默认走清华镜像；海外机器可覆盖：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用 Docker 层缓存：只改业务代码时不会重新装包
COPY requirements.txt ./
RUN pip install --no-cache-dir -i "${PIP_INDEX_URL}" -r requirements.txt

COPY . .

# 非 root 运行，降低容器逃逸风险
RUN useradd --create-home --uid 10001 bot && mkdir -p /app/data /app/logs && chown -R bot:bot /app
USER bot

CMD ["python", "bot.py"]
