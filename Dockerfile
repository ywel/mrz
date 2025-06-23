FROM python:3.12.3-slim-bullseye


ENV PYTHONUNBUFFERED=1

RUN apt-get update \
  && apt-get -y upgrade \
  && apt-get -y install tesseract-ocr ffmpeg libsm6 libxext6 --no-install-recommends \
  && apt-get clean \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt
COPY mrz.traineddata /usr/share/tesseract-ocr/5/tessdata/
COPY main.py .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]