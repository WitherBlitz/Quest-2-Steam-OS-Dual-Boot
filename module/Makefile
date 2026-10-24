# Out-of-tree build of the loader and the retained-log reader.
#
#   make -C module KDIR=<android kernel build dir> KSRC=<android kernel source>
#
# KDIR must be a fully built (Image + modules) output directory of the kernel
# the headset is running, so Module.symvers carries the right CRCs.
ifneq ($(KERNELRELEASE),)
obj-m += quest_kexec.o marker_read.o
obj-m += usb_log.o
obj-m += camera_dsp_diag.o
ifeq ($(QKX_OWNER_SELFTEST),1)
obj-m += owner_gate_selftest.o
endif
quest_kexec-y := loader.o transition.o shutdown.o shutdown_io.o
# Default builds stage only. Hardware actions require an explicit rebuild and
# a separate board shutdown/memory review; this flag does not establish safety.
ifeq ($(QKX_EXPERIMENTAL),1)
ccflags-y += -DQKX_ENABLE_EXPERIMENTAL_JUMP
endif
else
KDIR ?= $(error set KDIR to the built Android kernel output directory)
LOCALVERSION ?=
KMAKE = $(MAKE) -C $(abspath $(KDIR)) M=$(CURDIR) ARCH=arm64 CC=clang LD=ld.lld \
	HOSTCC=clang CROSS_COMPILE=aarch64-linux-gnu- CLANG_TRIPLE=aarch64-linux-gnu- \
	LOCALVERSION=$(LOCALVERSION)
.PHONY: all clean
all:
	$(KMAKE) modules
clean:
	$(KMAKE) clean
endif
