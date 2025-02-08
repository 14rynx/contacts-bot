FROM python:3.11-alpine

RUN apk update
RUN apk add git

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ .
RUN mkdir -p data

CMD ["python", "./main.py"]