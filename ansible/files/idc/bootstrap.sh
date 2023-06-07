#!/bin/sh

email='idc@galaxyproject.org'
username='idc'
password='PBKDF2$sha256$100000$XhmbiqICQVhoO+7z$kdb1UThcjcvljNvpdCCUVYU9EZwG2sQG'
database='idc'

sql="
INSERT INTO galaxy_user
    (create_time, update_time, email, username, password, last_password_change, external, deleted, purged, active)
VALUES
    (NOW(), NOW(), '$email', '$username', '$password', NOW(), false, false, false, true)
"

if [ $(psql -At -c "select count(*) from galaxy_user where username = '$username'" "$database") -eq 0 ]; then
    psql -c "$sql" "$database"
fi
