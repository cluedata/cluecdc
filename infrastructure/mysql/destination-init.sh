#!/bin/bash
set -euo pipefail
mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE USER IF NOT EXISTS 'delivery_mysql'@'%' IDENTIFIED BY '${MYSQL_DELIVERY_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, INDEX ON analytics_mysql.* TO 'delivery_mysql'@'%';
FLUSH PRIVILEGES;
SQL
