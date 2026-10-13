// Anteprima delle schermate sul Mac: scrive un PBM per ogni caso di prova.
//   cc -I main tools/screen_preview.c main/screen.c main/font_grotesk.c -o /tmp/preview && /tmp/preview <dir>
#include "screen.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "config.h"

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

    // schermata Oggi: immagine dal file, o a righe di prova
    static uint8_t digest[SB_DIGEST_BYTES];
    memset(digest, 0, sizeof(digest));
    bool loaded = false;
    if (argc > 2) {
        FILE *d = fopen(argv[2], "rb");
        if (d) { loaded = fread(digest, 1, sizeof(digest), d) == sizeof(digest); fclose(d); }
        if (!loaded) fprintf(stderr, "digest non leggibile: %s\n", argv[2]);
    }
    if (!loaded)
        for (int y = 0; y < SB_DIGEST_H; y++)
            if ((y / 8) % 2 == 0) memset(&digest[y * (SB_DIGEST_W / 8)], 0xAA, SB_DIGEST_W / 8);
    const screen_status_t today = { "15:45", SCREEN_BAT_DISCHARGING, 77, "FASTWEB-45HT2K", 0, "sync ok", false };
    screen_status_t alert = { "07:30", SCREEN_BAT_DISCHARGING, 8, NULL, 3, "no sync", true };
    const screen_status_t alert_net = { "07:30", SCREEN_BAT_DISCHARGING, 8, "FASTWEB-45HT2K", 3, "token ko", true };
    screen_render_today(fb, &today, digest);
    save(dir, "today", fb);
    screen_render_today(fb, &alert, digest);
    save(dir, "today-alert", fb);
    screen_render_today(fb, &alert_net, digest);
    save(dir, "today-alert-rete", fb);
    return 0;
}
