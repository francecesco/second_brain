// Anteprima delle schermate sul Mac: scrive un PBM per ogni caso di prova.
//   cc -I main tools/screen_preview.c main/screen.c main/font_grotesk.c -o /tmp/preview && /tmp/preview <dir>
#include "screen.h"
#include <stdio.h>

static void save(const char *dir, const char *name, const uint8_t *fb)
{
    char path[512];
    snprintf(path, sizeof(path), "%s/%s.pbm", dir, name);
    FILE *f = fopen(path, "wb");
    if (!f) { perror(path); return; }
    fprintf(f, "P4\n%d %d\n", SCREEN_W, SCREEN_H);
    fwrite(fb, 1, SCREEN_FB_BYTES, f);
    fclose(f);
}

int main(int argc, char **argv)
{
    const char *dir = argc > 1 ? argv[1] : ".";
    static uint8_t fb[SCREEN_FB_BYTES];
    const struct { const char *name; screen_status_t st; } cases[] = {
        { "batteria",  { "15:45", SCREEN_BAT_DISCHARGING, 77, "FASTWEB-45H7P7", 0, "sync ok" } },
        { "carica",    { "09:02", SCREEN_BAT_CHARGING, 84, "iPhone di Francesco", 2, "2 inviate" } },
        { "piena",     { "23:59", SCREEN_BAT_FULL, 100, "Advenias", 0, "sync ok" } },
        { "scarica",   { "07:30", SCREEN_BAT_DISCHARGING, 8, NULL, 3, "no sync" } },
        { "usb",       { "--:--", SCREEN_BAT_NONE, -1, NULL, 1, "Batteria scarica" } },
    };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        screen_render_status(fb, &cases[i].st);
        save(dir, cases[i].name, fb);
    }
    const char *const dev[] = { "DEV MODE", "192.168.0.31", "wifi: Advenias", "v0.7.9" };
    screen_render_lines(fb, dev, 4);
    save(dir, "dev", fb);
    return 0;
}
