FROM python:3.11-slim
WORKDIR /job
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Data is mounted at /job/data by the scheduler; the sink is declared in research2prod.toml.
ENTRYPOINT ["python", "run.py"]
