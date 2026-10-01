#!/bin/sh
# Restores a Postgres backup from S3 into the running "postgres" container.
# Usage: ./restore.sh <s3-key-or-date-prefix>
#   e.g. ./restore.sh 2026-09-30T03-17-00Z
#        ./restore.sh postgres/2026-09-30T03-17-00Z.sql.gz
#
# Run from the backup container: docker compose exec backup /usr/local/bin/restore.sh <key>
# WARNING: this drops and recreates the "beacon" database. Make sure that's what you want.
set -eu

: "${S3_BACKUP_BUCKET:?S3_BACKUP_BUCKET is not set}"
prefix="${S3_BACKUP_PREFIX:-postgres}"

arg="${1:?Usage: restore.sh <s3-key-or-date-prefix>}"
case "$arg" in
  */*) key="$arg" ;;
  *.sql.gz) key="${prefix}/${arg}" ;;
  *) key="${prefix}/${arg}.sql.gz" ;;
esac

file="/tmp/restore.sql.gz"
echo "[restore] downloading s3://${S3_BACKUP_BUCKET}/${key}"
aws s3 cp "s3://${S3_BACKUP_BUCKET}/${key}" "$file"

echo "[restore] this will DROP and recreate the 'beacon' database. Press Ctrl+C within 5s to abort."
sleep 5

echo "[restore] dropping and recreating database..."
PGPASSWORD="$POSTGRES_PASSWORD" psql -h postgres -U beacon -d postgres -c "DROP DATABASE IF EXISTS beacon;"
PGPASSWORD="$POSTGRES_PASSWORD" psql -h postgres -U beacon -d postgres -c "CREATE DATABASE beacon OWNER beacon;"

echo "[restore] loading dump..."
gunzip -c "$file" | PGPASSWORD="$POSTGRES_PASSWORD" psql -h postgres -U beacon -d beacon

rm -f "$file"
echo "[restore] done."
