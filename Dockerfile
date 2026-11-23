FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app
COPY pyproject.toml README.md ./
COPY doubao_checkin ./doubao_checkin
COPY checkin.py ./
RUN pip install --no-cache-dir .

ENV DOUBAO_DATA_DIR=/data
VOLUME ["/data"]

ENTRYPOINT ["python", "checkin.py"]
CMD ["run", "--block-resources"]
