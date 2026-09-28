FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models \
    TOKENIZERS_PARALLELISM=false
WORKDIR /app
# CPU-only PyTorch: the default wheel on x86 would pull several GB of CUDA libraries.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY service.py ./
EXPOSE 8080
# The model downloads into the /models volume on first start (several minutes), then loads
# from disk in seconds.
CMD ["uvicorn", "service:app", "--host", "0.0.0.0", "--port", "8080"]
