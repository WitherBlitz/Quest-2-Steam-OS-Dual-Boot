#!/bin/busybox sh
# Native Linux rescue PID1. Uses only RAM and a USB network gadget.
# Default RNDIS supports the development Windows host; qkx.usb=ecm selects ECM.
# No Android assembly, userdata mount or unported /proc/qkx_bootdone dependency.

qkx_log() { printf 'qkx-linux: %s\n' "$*"; }

qkx_idle() {
	qkx_log "$*; keeping PID1 alive"
	while :; do sleep 60; done
}

qkx_setup_usb() {
	qkx_config=$1
	qkx_sys=$2
	qkx_mode=$3
	case "$qkx_mode" in ecm|rndis) ;; *) return 2 ;; esac
	qkx_g="$qkx_config/usb_gadget/quest2"
	qkx_f="$qkx_g/functions/$qkx_mode.usb0"
	mkdir -p "$qkx_g/strings/0x409" "$qkx_g/configs/c.1/strings/0x409" "$qkx_f" || return 1
	printf '0x1d6b\n' > "$qkx_g/idVendor" || return 1
	printf '0x0104\n' > "$qkx_g/idProduct" || return 1
	printf '0x0200\n' > "$qkx_g/bcdUSB" || return 1
	printf 'Q2-LINUX\n' > "$qkx_g/strings/0x409/serialnumber" || return 1
	printf 'Quest 2\n' > "$qkx_g/strings/0x409/manufacturer" || return 1
	printf 'Quest 2 Linux rescue\n' > "$qkx_g/strings/0x409/product" || return 1
	printf '%s\n' "$qkx_mode" > "$qkx_g/configs/c.1/strings/0x409/configuration" || return 1
	printf '250\n' > "$qkx_g/configs/c.1/MaxPower" || return 1
	printf '02:51:4b:58:00:02\n' > "$qkx_f/dev_addr" || return 1
	printf '02:51:4b:58:00:01\n' > "$qkx_f/host_addr" || return 1
	if [ "$qkx_mode" = rndis ]; then
		printf '0xef\n' > "$qkx_g/bDeviceClass" || return 1
		printf '0x02\n' > "$qkx_g/bDeviceSubClass" || return 1
		printf '0x01\n' > "$qkx_g/bDeviceProtocol" || return 1
		mkdir -p "$qkx_g/os_desc" "$qkx_f/os_desc/interface.rndis" || return 1
		printf 'RNDIS\n' > "$qkx_f/os_desc/interface.rndis/compatible_id" || return 1
		printf '1\n' > "$qkx_g/os_desc/use" || return 1
		printf '0xcd\n' > "$qkx_g/os_desc/b_vendor_code" || return 1
		printf 'MSFT100\n' > "$qkx_g/os_desc/qw_sign" || return 1
		ln -s "$qkx_g/configs/c.1" "$qkx_g/os_desc/c.1" || return 1
	fi
	ln -s "$qkx_f" "$qkx_g/configs/c.1/$qkx_mode.usb0" || return 1
	for qkx_attempt in 1 2 3 4 5 6 7 8 9 10; do
		qkx_udc=
		for qkx_c in "$qkx_sys"/class/udc/*; do
			[ -d "$qkx_c" ] || continue
			[ -z "$qkx_udc" ] || { qkx_log 'multiple USB controllers; refusing to guess'; return 1; }
			qkx_udc=${qkx_c##*/}
		done
		if [ -n "$qkx_udc" ]; then
			printf '%s\n' "$qkx_udc" > "$qkx_g/UDC" || return 1
			qkx_log "bound $qkx_mode to $qkx_udc"
			return 0
		fi
		qkx_log "waiting for USB controller ($qkx_attempt/10)"
		sleep 1
	done
	return 1
}

qkx_shell_once() {
	# USB only: do not expose the root rescue shell on another network interface.
	telnetd -F -b 10.42.0.2:23 -l /bin/sh
}

qkx_main() {
	export PATH=/bin:/sbin
	export HOME=/root
	export TERM=vt100
	/bin/busybox --install -s /bin
	mount -t proc proc /proc || qkx_idle 'cannot mount proc'
	mount -t sysfs sysfs /sys || qkx_idle 'cannot mount sysfs'
	mount -t devtmpfs devtmpfs /dev || qkx_idle 'cannot mount devtmpfs'
	mkdir -p /dev/pts /config /tmp || qkx_idle 'cannot create runtime directories'
	mount -t devpts devpts /dev/pts || qkx_idle 'cannot mount devpts'
	mount -t tmpfs -o size=16m tmpfs /tmp || qkx_idle 'cannot mount runtime tmpfs'
	exec >/dev/kmsg 2>&1
	qkx_log "native PID1 reached $(uname -r)"
	mount -t configfs configfs /config || qkx_idle 'cannot mount configfs'
	qkx_mode=rndis
	for qkx_arg in $(cat /proc/cmdline); do
		case "$qkx_arg" in qkx.usb=*) qkx_mode=${qkx_arg#qkx.usb=} ;; esac
	done
	qkx_setup_usb /config /sys "$qkx_mode" || qkx_idle 'USB gadget setup failed'
	ifconfig lo 127.0.0.1 up || qkx_idle 'cannot bring up loopback'
	ifconfig usb0 10.42.0.2 netmask 255.255.255.0 up || qkx_idle 'cannot configure USB address'
	cat > /tmp/udhcpd.conf <<'EOC'
interface usb0
start 10.42.0.1
end 10.42.0.1
max_leases 1
lease_file /tmp/udhcpd.leases
option subnet 255.255.255.0
option lease 3600
EOC
	udhcpd -f /tmp/udhcpd.conf &
	# Replace a bounded snapshot rather than appending an unlimited log in RAM.
	(while :; do dmesg -s 262144 > /tmp/kernel.log; sleep 5; done) &
	qkx_log 'rescue shell: 10.42.0.2:23; host DHCP address 10.42.0.1'
	qkx_log 'kernel log: /tmp/kernel.log; manual reset: reboot -f'
	qkx_log 'hardware boot and recovery remain unvalidated; no boot watchdog disarmed'
	while :; do
		qkx_shell_once
		qkx_log "rescue listener exited ($?); restarting"
		sleep 1
	done
}

qkx_main "$@"
