# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rewardservice.py .
COPY lua ./lua

EXPOSE 8080
CMD ["gunicorn", "-w", "4", "-k", "gevent", "--worker-connections", "200", "-b", "0.0.0.0:8080", "--timeout", "30", "--access-logfile", "-", "rewardservice:app"]
