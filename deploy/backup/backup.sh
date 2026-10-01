#!/bin/sh
# Dumps the "beacon" Postgres database and uploads it to S3.
# Required env: POSTGRES_PASSWORD, S3_BACKUP_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY.
# Optional env: AWS_DEFAULT_REGION, S3_BACKUP_PREFIX (default: postgres), BACKUP_RETENTION_DAYS (default: 14).
set -eu

if [ -z "${S3_BACKUP_BUCKET:-}" ]; then
  echo "[backup] S3_BACKUP_BUCKET is not set, skipping (fine for local development)."
  exit 0
fi
prefix="${S3_BACKUP_PREFIX:-postgres}"
retention_days="${BACKUP_RETENTION_DAYS:-14}"

stamp=$(date -u +%Y-%m-%dT%H-%M-%SZ)
file="/tmp/beacon-${stamp}.sql.gz"

echo "[backup] dumping beacon database..."
PGPASSWORD="$POSTGRES_PASSWORD" pg_dump -h postgres -U beacon -d beacon | gzip > "$file"

echo "[backup] uploading to s3://${S3_BACKUP_BUCKET}/${prefix}/${stamp}.sql.gz"
aws s3 cp "$file" "s3://${S3_BACKUP_BUCKET}/${prefix}/${stamp}.sql.gz"
rm -f "$file"

echo "[backup] pruning backups older than ${retention_days} days..."
cutoff_epoch=$(( $(date -u +%s) - retention_days * 86400 ))
cutoff=$(date -u -d "@${cutoff_epoch}" +%Y-%m-%d)
aws s3 ls "s3://${S3_BACKUP_BUCKET}/${prefix}/" | while read -r _ _ _ key; do
  [ -z "$key" ] && continue
  file_date="${key%%T*}"
  if [ "$file_date" \< "$cutoff" ]; then
    echo "[backup] deleting old backup: $key"
    aws s3 rm "s3://${S3_BACKUP_BUCKET}/${prefix}/${key}"
  fi
done

echo "[backup] done."
