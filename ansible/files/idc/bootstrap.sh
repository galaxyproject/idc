#!/bin/sh

email='idc@galaxyproject.org'
username='idc'
password='PBKDF2$sha256$100000$XhmbiqICQVhoO+7z$kdb1UThcjcvljNvpdCCUVYU9EZwG2sQG'
database='idc'
sleep_time=5
sleep_count=30

sql="
INSERT INTO galaxy_user
    (create_time, update_time, email, username, password, last_password_change, external, deleted, purged, active)
VALUES
    (NOW(), NOW(), '$email', '$username', '$password', NOW(), false, false, false, true)
"

count=0
while [ $(psql -At -c "SELECT EXISTS (SELECT relname FROM pg_class WHERE relname = 'galaxy_user')" "$database") = 'f' ]; do
    echo "waiting for galaxy_user table..."
    count=$((count + 1))
    [ $count -lt $sleep_count ] || { echo "timed out"; exit 1; }
    sleep $sleep_time
done

if [ $(psql -At -c "SELECT count(*) FROM galaxy_user WHERE username = '$username'" "$database") -eq 0 ]; then
    psql -c "$sql" "$database"
fi
