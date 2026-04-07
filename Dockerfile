FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY . .
RUN mkdir -p uploads
EXPOSE 8081
CMD ["gunicorn", "--bind", "0.0.0.0:8081", "--workers", "2", "--timeout", "120", "app:app"]
