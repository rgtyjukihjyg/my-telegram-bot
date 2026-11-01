FROM python:3.11-slim

WORKDIR /app

# Устанавливаем supervisor для управления двумя процессами
RUN apt-get update && apt-get install -y supervisor && rm -rf /var/lib/apt/lists/*

# Сначала копируем только requirements, чтобы использовать кэш слоев Docker
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Копируем конфиг supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Открываем порт 8080, который требует RelaxDev
EXPOSE 8080

# Запускаем supervisor, который поднимет и webapp, и bot
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
