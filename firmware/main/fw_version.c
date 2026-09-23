#include "fw_version.h"
#include <stddef.h>

// version.txt e' embeddato come simbolo binario da CMake (EMBED_TXTFILES, NUL-terminato).
extern const char version_txt_start[] asm("_binary_version_txt_start");

const char *fw_version(void)
{
    // Il file puo' finire con '\n': copia una volta in un buffer senza spazi finali,
    // cosi' la versione e' pulita nei log, negli header HTTP e nel JSON di /status.
    static char s_ver[32] = {0};
    if (s_ver[0] == '\0') {
        size_t n = 0;
        while (n < sizeof(s_ver) - 1 && version_txt_start[n] &&
               version_txt_start[n] != '\n' && version_txt_start[n] != '\r') {
            s_ver[n] = version_txt_start[n];
            n++;
        }
        s_ver[n] = '\0';
    }
    return s_ver;
}
