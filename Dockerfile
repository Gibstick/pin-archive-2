FROM python:3.9-slim-buster

RUN useradd -ms /bin/sh pythonuser

USER pythonuser

WORKDIR /app

COPY requirements.txt ./

RUN pip install --no-cache-dir --no-warn-script-location -r requirements.txt

COPY *.py ./

CMD ["python", "bot.py"]
