#!/bin/sh
# Start the test broker host: install the files docs/remote.md describes, from the test's
# mount at /run/test, then run syslog and sshd.
set -eu

# One authorized_keys line for the test workstation, root-owned so the account can't
# add keys. The key may reach "prod" only, so "test" is refused.
install -d -o root -g root -m 755 /home/aft-broker/.ssh
printf 'restrict,command="/usr/local/bin/aft-mcp-broker laptop prod" %s\n' \
    "$(cat /run/test/client_key.pub)" > /home/aft-broker/.ssh/authorized_keys
chown root:root /home/aft-broker/.ssh/authorized_keys
chmod 644 /home/aft-broker/.ssh/authorized_keys

# The instance's settings: root:aft-broker 0640, in a directory only they can enter.
install -d -o root -g aft-broker -m 750 /etc/aft-broker
install -o root -g aft-broker -m 640 /run/test/prod.env /etc/aft-broker/prod.env

# Debian's syslog.conf sends the auth facility to /var/log/auth.log.
busybox syslogd
exec /usr/sbin/sshd -D -e
