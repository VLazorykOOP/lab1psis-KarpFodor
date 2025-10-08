FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y nginx && rm -rf /var/lib/apt/lists/*

COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

ENV SITE_OUT_DIR=/app/site
RUN python app.py --generate

COPY nginx.conf /etc/nginx/sites-enabled/default

EXPOSE 80 8000

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
