#include "wifi_bssid.h"

static int hexval(char c)
{
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return -1;
}

bool wifi_parse_bssid(const char *text, uint8_t out[6])
{
    if (!text || !out) {
        return false;
    }
    const char *p = text;
    for (int i = 0; i < 6; i++) {
        int hi = hexval(p[0]);
        int lo = (hi >= 0) ? hexval(p[1]) : -1;
        if (hi < 0 || lo < 0) {
            return false;
        }
        out[i] = (uint8_t)((hi << 4) | lo);
        p += 2;
        if (i < 5) {
            if (*p != ':' && *p != '-') {
                return false;
            }
            p++;
        }
    }
    return *p == '\0';
}
