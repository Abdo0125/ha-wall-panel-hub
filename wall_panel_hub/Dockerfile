ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY wallpanel_hub ./wallpanel_hub

CMD ["python3", "-m", "wallpanel_hub"]
