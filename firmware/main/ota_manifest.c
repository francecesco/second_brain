#include "ota_manifest.h"
#include "cJSON.h"
#include <stdio.h>
#include <string.h>

bool ota_manifest_parse(const char *json, ota_manifest_t *out) {
    cJSON *root = cJSON_Parse(json);
    if (!root) return false;
    cJSON *v = cJSON_GetObjectItem(root, "version");
    cJSON *u = cJSON_GetObjectItem(root, "url");
    cJSON *s = cJSON_GetObjectItem(root, "sha256");
    bool ok = cJSON_IsString(v) && cJSON_IsString(u) && cJSON_IsString(s);
    if (ok) {
        /* snprintf tronca silenziosamente un campo troppo lungo pur
         * ritornando "successo": per un hash/URL OTA una troncatura silente
         * e' peggio di un rifiuto esplicito, quindi verifichiamo prima che
         * ogni campo entri nel buffer di destinazione. */
        ok = strlen(v->valuestring) < sizeof(out->version) &&
             strlen(u->valuestring) < sizeof(out->url) &&
             strlen(s->valuestring) < sizeof(out->sha256);
    }
    if (ok) {
        snprintf(out->version, sizeof(out->version), "%s", v->valuestring);
        snprintf(out->url, sizeof(out->url), "%s", u->valuestring);
        snprintf(out->sha256, sizeof(out->sha256), "%s", s->valuestring);
    }
    cJSON_Delete(root);
    return ok;
}

int ota_semver_cmp(const char *a, const char *b) {
    int a1=0,a2=0,a3=0,b1=0,b2=0,b3=0;
    sscanf(a, "%d.%d.%d", &a1,&a2,&a3);
    sscanf(b, "%d.%d.%d", &b1,&b2,&b3);
    if (a1!=b1) return a1<b1?-1:1;
    if (a2!=b2) return a2<b2?-1:1;
    if (a3!=b3) return a3<b3?-1:1;
    return 0;
}

bool ota_should_update(const char *current, const ota_manifest_t *m) {
    return ota_semver_cmp(current, m->version) < 0;
}
