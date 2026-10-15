# The recipe Google Cloud uses to package MosshyBot into a "container":
# a small, self-contained Linux computer image with Python and the bot inside.

FROM python:3.14-slim

# Show Python's log output straight away (so it appears in Cloud Run's logs).
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install the libraries first. Docker remembers this step, so later deploys
# that only change the code skip it and finish faster.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY *.py ./

# Cloud Run tells the bot which port to listen on in $PORT (usually 8080).
CMD exec uvicorn cloud_server:web --host 0.0.0.0 --port ${PORT:-8080}
