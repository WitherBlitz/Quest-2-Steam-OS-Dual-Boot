/* Execute exact pinned FastRPC bodies with controlled transport/SCM results. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef ECHRNG
#define ECHRNG 44
#endif

typedef struct { struct { void *pv; size_t len; } buf; } remote_arg_t;
struct fastrpc_ioctl_invoke_crc {
	struct { uint32_t handle, sc; remote_arg_t *pra; } inv;
	int *fds;
	unsigned int *attrs;
	uint32_t *crc;
};
struct mutex { int unused; };
struct secure_vm { int *vmid, *vmperm, vmcount; };
struct fastrpc_channel_ctx { struct secure_vm rhvm; struct mutex smd_mutex; };
struct fastrpc_apps { int compat; struct fastrpc_channel_ctx channel[4]; };
struct fastrpc_file { int tgid, cid; struct fastrpc_apps *apps; struct mutex map_mutex; };
struct smq_phy_page { uint64_t addr; size_t size; };
static struct fastrpc_apps gfa;
static int hlosvm[1], remote_vmid[1], remote_perm[1];
static int invoke_error, unmap_error, hyp_error, invoke_calls, hyp_calls, error_logs;
static int lock_depth, lock_calls, unlock_calls;

#define ADSP_DOMAIN_ID 0
#define NUM_CHANNELS 4
#define ADSP_MMAP_HEAP_ADDR 4
#define ADSP_MMAP_REMOTE_HEAP_ADDR 8
#define FASTRPC_STATIC_HANDLE_PROCESS_GROUP 1
#define FASTRPC_MODE_PARALLEL 1
#define VMID_HLOS 0
#define PERM_READ 1
#define PERM_WRITE 2
#define PERM_EXEC 4
#define REMOTE_SCALARS_MAKE(method, in, out) (method)
#define VERIFY(err, val) do { if ((val) == 0) (err) = (err) == 0 ? -1 : (err); } while (0)
#define pr_err(...) do { error_logs++; } while (0)

static void mutex_lock(struct mutex *lock)
{
	(void)lock;
	lock_depth++;
	lock_calls++;
}
static void mutex_unlock(struct mutex *lock)
{
	(void)lock;
	lock_depth--;
	unlock_calls++;
}

static int fastrpc_internal_invoke(struct fastrpc_file *fl, uint32_t mode,
				 uint32_t kernel, struct fastrpc_ioctl_invoke_crc *inv)
{
	(void)fl;
	if (mode != FASTRPC_MODE_PARALLEL || kernel != 1 ||
	    inv->inv.handle != FASTRPC_STATIC_HANDLE_PROCESS_GROUP)
		abort();
	invoke_calls++;
	if (inv->inv.sc == 3 || inv->inv.sc == 5)
		return unmap_error;
	if (inv->inv.sc == 2 || inv->inv.sc == 4)
		*(uintptr_t *)inv->inv.pra[2].buf.pv = 0x12340000;
	return invoke_error;
}

static int hyp_assign_phys(uint64_t phys, uint64_t size, int *src, int nsrc,
			   int *dest, int *perms, int ndest)
{
	(void)phys; (void)size; (void)src; (void)nsrc;
	(void)dest; (void)perms; (void)ndest;
	hyp_calls++;
	return hyp_error;
}

/* SOURCE */

static int check(int got, int wanted, const char *label)
{
	if (got == wanted) return 0;
	fprintf(stderr, "%s: got %d expected %d\n", label, got, wanted);
	return 1;
}

int main(int argc, char **argv)
{
	struct fastrpc_file fl = { .tgid = 123, .cid = 0, .apps = &gfa };
	uintptr_t raddr = 0;
	int result, expected;
	if (argc != 2) return 100;
	gfa.channel[0].rhvm = (struct secure_vm) {
		.vmid = remote_vmid, .vmperm = remote_perm, .vmcount = 1
	};

	if (!strcmp(argv[1], "heap_error") || !strcmp(argv[1], "heap_error_locked")) {
		int locked = !strcmp(argv[1], "heap_error_locked");
		invoke_error = -ETIMEDOUT;
		lock_depth = locked ? 2 : 0;
		result = fastrpc_munmap_on_dsp_rh(&fl, 0x2000, 4096,
						ADSP_MMAP_HEAP_ADDR, locked);
#ifdef CONFIG_QKX_OWNER_GATE
		expected = -ETIMEDOUT;
#else
		expected = 0;
#endif
		return check(result, expected, "heap release result") ||
		       check(invoke_calls, 1, "heap invoke count") ||
		       check(lock_depth, locked ? 2 : 0, "restored lock depth") ||
		       check(lock_calls, locked ? 2 : 0, "lock count") ||
		       check(unlock_calls, locked ? 2 : 0, "unlock count");
	}
	if (!strcmp(argv[1], "heap_success")) {
		result = fastrpc_munmap_on_dsp_rh(&fl, 0x2000, 4096,
						ADSP_MMAP_HEAP_ADDR, 0);
		return check(result, 0, "heap success") || check(invoke_calls, 1, "invoke count");
	}
	if (!strcmp(argv[1], "remote_heap_error")) {
		hyp_error = -EACCES;
		result = fastrpc_munmap_on_dsp_rh(&fl, 0x2000, 4096,
						ADSP_MMAP_REMOTE_HEAP_ADDR, 0);
		return check(result, -EACCES, "remote secure return") ||
		       check(hyp_calls, 1, "secure return count") ||
		       check(invoke_calls, 0, "no release RPC for remote heap");
	}
	if (!strcmp(argv[1], "map_assign_rollback_ok") ||
	    !strcmp(argv[1], "map_assign_rollback_error")) {
		hyp_error = -EACCES;
		unmap_error = !strcmp(argv[1], "map_assign_rollback_error") ? -ETIMEDOUT : 0;
		result = fastrpc_mmap_on_dsp(&fl, ADSP_MMAP_REMOTE_HEAP_ADDR,
					    0, 0x2000, 4096, 1, &raddr);
#ifdef CONFIG_QKX_OWNER_GATE
		expected = -EACCES;
#else
		expected = unmap_error;
#endif
		return check(result, expected, "original secure assignment failure") ||
		       check(invoke_calls, 2, "map and compensating unmap count") ||
		       check(hyp_calls, 1, "assignment count") ||
		       check(error_logs, unmap_error ? 2 : 1, "both failures logged");
	}
	if (!strcmp(argv[1], "map_send_error")) {
		invoke_error = -ECONNRESET;
		result = fastrpc_mmap_on_dsp(&fl, ADSP_MMAP_REMOTE_HEAP_ADDR,
					    0, 0x2000, 4096, 1, &raddr);
		return check(result, -ECONNRESET, "initial map failure") ||
		       check(invoke_calls, 1, "no compensation after failed map") ||
		       check(hyp_calls, 0, "no secure assign after failed map");
	}
	if (!strcmp(argv[1], "map_success")) {
		result = fastrpc_mmap_on_dsp(&fl, ADSP_MMAP_REMOTE_HEAP_ADDR,
					    0, 0x2000, 4096, 1, &raddr);
		return check(result, 0, "map success") ||
		       check(raddr == 0x12340000, 1, "returned remote address") ||
		       check(invoke_calls, 1, "map count") ||
		       check(hyp_calls, 1, "assignment count") ||
		       check(error_logs, 0, "no errors");
	}
	return 101;
}
