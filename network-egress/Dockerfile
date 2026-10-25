FROM python:3.12-slim
WORKDIR /app
COPY proxy.py .
USER 65534:65534
CMD ["python", "proxy.py"]
