FROM python:3.12-slim
WORKDIR /app
COPY pilot.py train.py server.py prospective.py monitor.py ./
COPY models/ ./models/
COPY web/ ./web/
RUN mkdir -p data/raw
ENV HOST=0.0.0.0 PORT=8787 PYTHONUNBUFFERED=1
EXPOSE 8787
CMD ["python", "server.py"]
