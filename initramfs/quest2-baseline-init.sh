#!/bin/busybox sh
# Noninteractive Linux build baseline. No listener or storage assembly.
export PATH=/bin:/sbin
export HOME=/root
export TERM=vt100
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts /tmp
mount -t devpts devpts /dev/pts
mount -t tmpfs tmpfs /tmp
echo 'Quest 2 Linux baseline; kexec handoff and hardware support are unvalidated.' >/dev/kmsg
while :; do
    sleep 60
done
