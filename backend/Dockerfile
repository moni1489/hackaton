FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
# Демо-БД едет в образе; при первом старте копируется в том, чтобы переживать пересоздание.
CMD ["sh", "-c", "[ -f /app/dbdata/hackathon.db ] || cp /app/hackathon.db /app/dbdata/; exec python main.py"]
