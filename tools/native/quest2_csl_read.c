/* SPDX-License-Identifier: GPL-2.0 */
/* One caller-specified sparse CSL scalar plan; no pointers are followed.
 * CLI: quest2_csl_read PID ELFBASE (decimal PID, hexadecimal base).
 * stdin: "QCSLR01\0", LE u32 count, count * { LE u64 address, LE u32 bytes }.
 * stdout: requested bytes concatenated in plan order; valid only on exit zero.
 * Exit: 2 CLI, 3 plan/input, 4 open, 5 pread/short read, 6 write, 7 close.
 * Zero records require EOF and succeed without opening process memory.
 * The host must enforce a timeout and its aggregate allowance across invocations.
 * /proc/pid/mem invokes remote VM access; sparse reads do not establish a
 * kernel lock deadline or absence of effects on the observed process.
 *
 * ABI source: oculus-linux-kernel 6bde5fc06b252c97761bc0963dd13c1abcf377ab,
 * arch/arm64/include/uapi/asm/unistd.h includes asm-generic/unistd.h:
 * openat=56, close=57, read=63, write=64, pread64=67, exit=93.
 * include/uapi/asm-generic/fcntl.h: O_RDONLY=0, O_CLOEXEC=02000000.
 * fs/proc/base.c:797-915: mem_open uses PTRACE_MODE_ATTACH access checks;
 * mem_read calls mem_rw(..., 0), FOLL_FORCE and access_remote_vm. It can
 * return zero, short data or errors. There is no ptrace syscall here.
 */
typedef __UINTPTR_TYPE__ qcs_word;
typedef __INTPTR_TYPE__ qcs_sword;
typedef unsigned long long qcs_u64;
typedef unsigned int qcs_u32;

#define QCS_MAX_REQUESTS 16384U
#define QCS_MAX_BYTES 1048576U
#define QCS_FIELD_BYTES 512U
#define QCS_ADDRESS_LIMIT (1ULL << 56)
#define QCS_GLOBAL 0x865f0ULL
#define QCS_EXTENT (0x1d94e0ULL + 128ULL * 0x388ULL)

struct qcs_request {
    qcs_u64 address;
    qcs_u32 bytes;
};

static struct qcs_request qcs_requests[QCS_MAX_REQUESTS];

#ifdef QCSL_HOST_TEST
static qcs_sword qcs_syscall(qcs_word nr, qcs_word a0, qcs_word a1,
                             qcs_word a2, qcs_word a3);
#else
#if !defined(__aarch64__)
#error "Build this freestanding reader for AArch64 Linux"
#endif
static qcs_sword qcs_syscall(qcs_word nr, qcs_word a0, qcs_word a1,
                             qcs_word a2, qcs_word a3)
{
    register qcs_word x0 __asm__("x0") = a0;
    register qcs_word x1 __asm__("x1") = a1;
    register qcs_word x2 __asm__("x2") = a2;
    register qcs_word x3 __asm__("x3") = a3;
    register qcs_word x8 __asm__("x8") = nr;
    __asm__ volatile("svc #0" : "+r"(x0)
                     : "r"(x1), "r"(x2), "r"(x3), "r"(x8) : "memory", "cc");
    return (qcs_sword)x0;
}
#endif

static qcs_u64 qcs_le(const unsigned char *bytes, unsigned int count)
{
    qcs_u64 value = 0;
    unsigned int i;
    for (i = 0; i < count; i++)
        value |= (qcs_u64)bytes[i] << (i * 8);
    return value;
}

static int qcs_number(const char *text, unsigned int radix, qcs_u64 *out)
{
    qcs_u64 value = 0;
    unsigned int digit;
    if (radix == 16 && text[0] == '0' && (text[1] == 'x' || text[1] == 'X'))
        text += 2;
    if (!*text)
        return 0;
    for (; *text; text++) {
        if (*text >= '0' && *text <= '9')
            digit = (unsigned int)(*text - '0');
        else if (radix == 16 && *text >= 'a' && *text <= 'f')
            digit = (unsigned int)(*text - 'a') + 10;
        else if (radix == 16 && *text >= 'A' && *text <= 'F')
            digit = (unsigned int)(*text - 'A') + 10;
        else
            return 0;
        if (digit >= radix || value > (~0ULL - digit) / radix)
            return 0;
        value = value * radix + digit;
    }
    *out = value;
    return 1;
}

static int qcs_input(unsigned char *buffer, unsigned int bytes)
{
    while (bytes) {
        qcs_sword got = qcs_syscall(63, 0, (qcs_word)buffer, bytes, 0);
        /* Pipe fragmentation is normal; EOF and errors (including EINTR)
         * are failures. Neither stdin nor process reads retry errors.
         */
        if (got <= 0 || (qcs_word)got > bytes)
            return 0;
        buffer += got;
        bytes -= (unsigned int)got;
    }
    return 1;
}

static int qcs_output(const unsigned char *buffer, unsigned int bytes)
{
    unsigned int interrupted = 0;
    while (bytes) {
        qcs_sword sent = qcs_syscall(64, 1, (qcs_word)buffer, bytes, 0);
        /* Also bound repeated EINTR locally. The required host timeout does
         * not establish a deadline for a thread blocked inside the kernel.
         */
        if (sent == -4 && interrupted++ < 64)
            continue;
        if (sent <= 0 || (qcs_word)sent > bytes)
            return 0;
        buffer += sent;
        bytes -= (unsigned int)sent;
    }
    return 1;
}

static int qcs_main(int argc, char **argv)
{
    static const unsigned char magic[8] = { 'Q', 'C', 'S', 'L', 'R', '0', '1', 0 };
    unsigned char record[12], field[QCS_FIELD_BYTES];
    char path[32], digits[8];
    qcs_u64 pid, base, low, high, decimal;
    qcs_u32 count, total = 0, i, used = 0, position = 0;
    qcs_sword fd, closed;
    int result = 0;

    if (argc != 3 || !qcs_number(argv[1], 10, &pid) || !pid || pid > 4194304 ||
        !qcs_number(argv[2], 16, &base) || (base & 4095) ||
        base >= QCS_ADDRESS_LIMIT - QCS_GLOBAL - QCS_EXTENT)
        return 2;
    low = base + QCS_GLOBAL;
    high = low + QCS_EXTENT;
    if (!qcs_input(record, sizeof(record)))
        return 3;
    for (i = 0; i < sizeof(magic); i++)
        if (record[i] != magic[i])
            return 3;
    count = (qcs_u32)qcs_le(record + 8, 4);
    if (count > QCS_MAX_REQUESTS)
        return 3;
    for (i = 0; i < count; i++) {
        qcs_u64 address;
        qcs_u32 bytes;
        if (!qcs_input(record, sizeof(record)))
            return 3;
        address = qcs_le(record, 8);
        bytes = (qcs_u32)qcs_le(record + 8, 4);
        /* Subtraction follows address checks, so neither address+bytes nor
         * budget arithmetic can wrap before validation.
         */
        if (!bytes || bytes > QCS_FIELD_BYTES || address < low || address >= high ||
            bytes > high - address || bytes > QCS_MAX_BYTES - total)
            return 3;
        qcs_requests[i].address = address;
        qcs_requests[i].bytes = bytes;
        total += bytes;
    }
    /* Require the host to close stdin. Reject extra bytes and read errors
     * before opening process memory or reading any requested field.
     */
    if (qcs_syscall(63, 0, (qcs_word)record, 1, 0) != 0)
        return 3;
    if (!count)
        return 0;

    path[position++] = '/'; path[position++] = 'p'; path[position++] = 'r';
    path[position++] = 'o'; path[position++] = 'c'; path[position++] = '/';
    decimal = pid;
    do {
        digits[used++] = (char)('0' + decimal % 10);
        decimal /= 10;
    } while (decimal);
    while (used)
        path[position++] = digits[--used];
    path[position++] = '/'; path[position++] = 'm'; path[position++] = 'e';
    path[position++] = 'm'; path[position] = 0;
    fd = qcs_syscall(56, (qcs_word)(qcs_sword)-100, (qcs_word)path, 02000000, 0);
    if (fd < 0)
        return 4;
    for (i = 0; i < count; i++) {
        qcs_u32 bytes = qcs_requests[i].bytes;
        qcs_sword got = qcs_syscall(67, (qcs_word)fd, (qcs_word)field, bytes,
                                     (qcs_word)qcs_requests[i].address);
        if (got != (qcs_sword)bytes) {
            result = 5;
            break;
        }
        if (!qcs_output(field, bytes)) {
            result = 6;
            break;
        }
    }
    closed = qcs_syscall(57, (qcs_word)fd, 0, 0, 0);
    return result ? result : (closed ? 7 : 0);
}

#ifndef QCSL_HOST_TEST
__attribute__((noreturn)) void qcs_start(qcs_word *stack)
{
    int result = qcs_main((int)stack[0], (char **)(stack + 1));
    qcs_syscall(93, (qcs_word)result, 0, 0, 0);
    for (;;)
        __asm__ volatile("brk #0");
}

__asm__(".text\n.global _start\n.type _start,%function\n_start:\n"
        "mov x0, sp\nbl qcs_start\n.size _start, .-_start\n");
#endif
