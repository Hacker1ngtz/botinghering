# Imagen base ligera de Python 3.12
FROM python:3.12-slim

# Variables de entorno
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Directorio de trabajo dentro del contenedor
WORKDIR /app

# Copiar todos los archivos del proyecto al contenedor
COPY . /app

# Instalar dependencias
RUN pip install --upgrade pip
RUN pip install --no-cache-dir python-binance pandas numpy python-dotenv colorama asyncio

# Comando para ejecutar tu bot
CMD ["python", "bot.py"]
