#include "fw_version.h"
// version.txt è embeddato come simbolo binario da CMake (Step 7).
extern const char version_txt_start[] asm("_binary_version_txt_start");
const char *fw_version(void) { return version_txt_start; }
