/* SPDX-License-Identifier: GPL-2.0-only */
/* QEMU virt only. No Quest device or firmware addresses. */
.equ HIGH_ALIAS, 0xffffff8000000000
.section .text.boot,"ax"
.global _start
_start:
    msr daifset, #0xf
    ldr x9, =_bss_start
    ldr x10, =_bss_end
1:
    cmp x9, x10
    b.hs 2f
    str xzr, [x9], #8
    b 1b
2:
    ldr x9, =_stack_top
    mov sp, x9
    adr x9, exception_vectors
    msr vbar_el1, x9
    mrs x9, CurrentEL
    cmp x9, #4
    b.ne exception_exit
    /* Two L1 roots: low identity RAM and a separate high alias of that RAM. */
    ldr x9, =initial_low
    ldr x10, =0x40000701
    str x10, [x9, #8]
    msr ttbr0_el1, x9
    ldr x9, =initial_high
    str x10, [x9, #8]
    msr ttbr1_el1, x9
    mov x9, #0xff
    msr mair_el1, x9
    /* Initial T0SZ=26 differs from the production transition's T0SZ=25. */
    ldr x9, =0x2b519351a
    msr tcr_el1, x9
    isb
    dsb ishst
    tlbi vmalle1
    dsb ish
    isb
    mrs x9, sctlr_el1
    orr x9, x9, #1
    orr x9, x9, #4
    orr x9, x9, #0x1000
    msr sctlr_el1, x9
    isb
    ldr x9, =HIGH_ALIAS
    add sp, sp, x9
    adr x10, high_start
    add x10, x10, x9
    br x10
high_start:
    bl harness_main
    b exception_exit

/* qkx_enter branches here with MMU off; establish a physical stack for C. */
.global simulated_kernel_entry
simulated_kernel_entry:
    ldr x9, =_stack_top
    mov sp, x9
    b entry_finish

.balign 2048
exception_vectors:
.rept 16
    b exception_exit
    .balign 128
.endr
exception_exit:
    mov x0, #4
    adr x1, exception_message
    hlt #0xf000
    mov x0, #0x18
    adr x1, exception_status
    hlt #0xf000
3:
    wfe
    b 3b
.balign 8
exception_status: .quad 0x20026, 2
exception_message: .asciz "FAIL exception\n"

.bss
.balign 4096
.global initial_low
initial_low: .skip 4096
.global initial_high
initial_high: .skip 4096
