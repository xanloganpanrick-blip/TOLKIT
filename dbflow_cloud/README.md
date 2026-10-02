# DBFLOW

Облачное рабочее пространство для обработки больших таблиц.

## Что уже есть

- регистрация и вход по логину/паролю;
- личное облачное хранилище пользователя;
- синхронизация файлов и истории задач между устройствами;
- история каждой обработки: входные файлы, статус, прогресс, лог, результаты;
- скачать файл;
- удалить файл;
- «Забрать» — скачать и после отправки удалить его из хранилища;
- XLSX/XLSM/CSV/Parquet → CSV;
- нормализация телефонов, ФИО и дат;
- удаление первой `7` в колонке телефона;
- дедупликация;
- merge нескольких таблиц;
- split большой таблицы на CSV-части внутри ZIP;
- анализ структуры файла;
- фиолетовый desktop-style UI;
- Polars для быстрой табличной обработки;
- локальное файловое хранилище или S3-compatible storage.

> Исходный HTML Toolkit был использован как ориентир по полезным операциям. Старые разрозненные вкладки заменены единой системой задач и файлов. В исходнике уже присутствовали отдельные операции merge/split и CSV-поддержка, а также самостоятельная функция удаления первой `7`; в DBFLOW эти возможности сведены в единый интерфейс.

## Самый простой запуск локально

```bash
docker build -t dbflow .
docker run --rm -p 8080:8080 \
  -e SECRET_KEY="change-this-secret" \
  -v dbflow_data:/data \
  dbflow
```

Откройте `http://localhost:8080`.

## GitHub → Railway

1. Распакуйте проект.
2. Создайте новый GitHub-репозиторий и загрузите **всё содержимое папки DBFLOW** в корень репозитория.
3. В Railway: **New Project → Deploy from GitHub repo**.
4. Railway автоматически увидит `Dockerfile` и `railway.json`.
5. Добавьте переменные:
   - `SECRET_KEY` — длинная случайная строка.
   - Для тестового/одиночного сервиса можно оставить `DATABASE_URL=sqlite:////data/dbflow.db`.
6. Для постоянного хранения файлов при локальном backend добавьте **Railway Volume**, смонтированный в `/data`.
7. Для реального многопользовательского продакшена добавьте Railway PostgreSQL и замените `DATABASE_URL` на выдаваемую Railway строку PostgreSQL.
8. Для глобального хранения лучше включить S3-compatible backend:
   - `STORAGE_BACKEND=s3`
   - `S3_ENDPOINT_URL`
   - `S3_REGION`
   - `S3_BUCKET`
   - `S3_ACCESS_KEY_ID`
   - `S3_SECRET_ACCESS_KEY`

## Почему CSV — основной результат

Для миллионов строк CSV проще, быстрее и не имеет лимита Excel в ~1.05 млн строк на лист. XLSX остаётся входным форматом, а тяжёлые результаты по умолчанию уходят в CSV.

## Важное про очень большие базы

Текущая версия уже использует Polars и обрабатывает таблицы сервером, а не браузером. Для файлов в десятки/сотни ГБ следующий шаг — multipart/resumable upload и отдельные worker-сервисы. Архитектура проекта специально разделена на `storage`, `jobs`, `table_ops`, поэтому это можно добавить без переписывания UI.

Для 7–8 ТБ не храните всё на Railway Volume. Используйте объектное хранилище (S3/R2/MinIO-compatible) и отдельные workers.

## Структура

```text
app/
  main.py       API + auth + files + jobs
  jobs.py       очередь/исполнение задач
  table_ops.py  быстрые операции над таблицами
  storage.py    local/S3 abstraction
  models.py     пользователи, файлы, история задач
  static/       frontend desktop workspace
Dockerfile
railway.json
```

## Что стоит добавить перед большим публичным запуском

- PostgreSQL вместо SQLite;
- Redis + отдельный worker вместо in-process thread;
- multipart/resumable uploads;
- presigned S3 uploads/downloads;
- quotas и лимиты аккаунтов;
- email/2FA/recovery;
- audit log;
- rate limiting;
- antivirus/archive-bomb checks для ZIP;
- distributed workers;
- Parquet/DuckDB pipeline для десятков миллиардов строк.
