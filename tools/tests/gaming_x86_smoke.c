/* Deliberately narrow dynamic x86-64 loader/libc/argv fixture, not a game. */
#include <stdio.h>
#include <string.h>
int main(int argc, char **argv)
{
    if (argc != 2 || strcmp(argv[1], "literal ; $(unused) '")) return 2;
    puts("QKX_X86_64_LIBC_OK");
    return 23;
}
