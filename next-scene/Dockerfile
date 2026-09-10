FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN pip install --no-cache-dir --no-deps .

ENV NEXT_SCENE_SHOW=0
EXPOSE 8080

CMD ["next-scene"]
