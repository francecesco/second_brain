#include "ota.h"
#include "ota_manifest.h"
#include "fw_version.h"

#include <string.h>
#include <ctype.h>
#include <stdlib.h>
#include <inttypes.h>

#include "esp_log.h"
#include "esp_http_client.h"
#include "esp_ota_ops.h"
#include "mbedtls/sha256.h"

static const char *TAG = "ota";

// Limite di sicurezza per la dimensione del manifest JSON (normalmente
// poche centinaia di byte); usato anche come capacita' di fallback quando
// il server non dichiara Content-Length (es. risposta chunked).
#define OTA_MANIFEST_MAX_LEN 4096
// Chunk di lettura per lo streaming del binario verso la partizione OTA.
#define OTA_DOWNLOAD_CHUNK 4096

// Scarica interamente il corpo di url e lo ritorna in *out_buf come stringa
// C NUL-terminata allocata sull'HEAP (il chiamante deve fare free() su
// ESP_OK). Pensato per risposte piccole (il manifest).
//
// Il buffer viene allocato con capacity+1 byte (capacity = Content-Length
// se il server lo dichiara, altrimenti OTA_MANIFEST_MAX_LEN di fallback),
// cosi' c'e' sempre spazio per il terminatore '\0' dopo l'ultimo byte letto
// senza sforare l'allocazione. *out_len riporta i byte effettivamente letti
// (accumulati nel loop, non assunti pari a capacity), e buf[total] = '\0'
// viene scritto usando quel totale reale: senza questo, cJSON_Parse (che
// richiede una stringa C valida) puo' leggere oltre la fine dei dati validi
// o non trovare mai un terminatore.
//
// Il loop di lettura chiama SEMPRE esp_http_client_read() prima di
// controllare esp_http_client_is_complete_data_received(): se il corpo
// intero arriva insieme agli header nello stesso segmento TCP,
// is_complete_data_received() puo' gia' risultare vero PRIMA di qualunque
// chiamata a read(), ma i byte vanno comunque prelevati con un read()
// esplicito (sono bufferizzati internamente dal parser HTTP, non ancora
// copiati nel nostro buffer). Controllare la condizione prima di leggere
// (bug del round precedente) faceva uscire dal loop con total==0 in quel
// caso, producendo in modo intermittente un manifest vuoto/troncato a
// seconda di come i dati arrivavano sulla socket.
static esp_err_t http_get_to_buffer(const char *url, char **out_buf, int *out_len, int64_t *out_content_length)
{
    *out_buf = NULL;
    *out_len = 0;
    *out_content_length = 0;

    esp_http_client_config_t config = {
        .url = url,
        .timeout_ms = 10000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return ESP_FAIL;
    }

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "http open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return err;
    }

    int64_t content_length = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status != 200) {
        ESP_LOGE(TAG, "manifest GET status %d", status);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }

    size_t capacity;
    if (content_length > 0) {
        if (content_length > OTA_MANIFEST_MAX_LEN) {
            ESP_LOGE(TAG, "manifest troppo grande: %lld byte (max %d)",
                     (long long)content_length, OTA_MANIFEST_MAX_LEN);
            esp_http_client_close(client);
            esp_http_client_cleanup(client);
            return ESP_ERR_INVALID_SIZE;
        }
        capacity = (size_t)content_length;
    } else {
        // Content-Length assente/sconosciuto (es. chunked): usa il tetto
        // fisso come capacita' massima.
        capacity = OTA_MANIFEST_MAX_LEN;
    }

    char *buf = malloc(capacity + 1); // +1 per il terminatore NUL
    if (!buf) {
        ESP_LOGE(TAG, "malloc buffer manifest fallita (%u byte)", (unsigned)(capacity + 1));
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_ERR_NO_MEM;
    }

    size_t total = 0;
    while (total < capacity) {
        int r = esp_http_client_read(client, buf + total, capacity - total);
        if (r < 0) {
            ESP_LOGE(TAG, "manifest read error");
            free(buf);
            esp_http_client_close(client);
            esp_http_client_cleanup(client);
            return ESP_FAIL;
        }
        if (r == 0) {
            // Nessun byte da questo read: o la connessione e' stata
            // chiusa, o non c'e' altro da leggere. Il controllo su
            // total (sotto, rispetto a content_length) distingue un EOF
            // legittimo da un download incompleto.
            break;
        }
        total += (size_t)r;
        if (esp_http_client_is_complete_data_received(client)) {
            break;
        }
        // Altrimenti: read parziale (short read, normale con TCP) ma il
        // corpo non e' ancora arrivato tutto -> si continua a leggere.
    }

    if (total == 0) {
        ESP_LOGE(TAG, "manifest fetch: 0 byte letti");
        free(buf);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    if (content_length > 0 && (int64_t)total != content_length) {
        ESP_LOGE(TAG, "manifest incompleto: %u/%lld byte letti",
                 (unsigned)total, (long long)content_length);
        free(buf);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    buf[total] = '\0'; // sempre in bounds: total <= capacity, buffer e' capacity+1

    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    *out_buf = buf;
    *out_len = (int)total;
    *out_content_length = content_length;
    return ESP_OK;
}

static void sha256_to_hex(const uint8_t digest[32], char out[65])
{
    static const char hexd[] = "0123456789abcdef";
    for (int i = 0; i < 32; i++) {
        out[i * 2]     = hexd[digest[i] >> 4];
        out[i * 2 + 1] = hexd[digest[i] & 0x0F];
    }
    out[64] = '\0';
}

// Scarica m->url in streaming, scrivendolo nella partizione OTA inattiva e
// calcolando in parallelo lo sha256. Verifica l'hash contro m->sha256 PRIMA
// di impostare il boot: se non combacia, aborta senza toccare la partizione
// di boot corrente e ritorna un errore. Se tutto va bene, non ritorna mai
// (esp_restart()).
static esp_err_t ota_download_and_apply(const ota_manifest_t *m)
{
    const esp_partition_t *update_partition = esp_ota_get_next_update_partition(NULL);
    if (!update_partition) {
        ESP_LOGE(TAG, "nessuna partizione OTA inattiva disponibile");
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "scrivo su partizione '%s' @0x%08" PRIx32,
             update_partition->label, update_partition->address);

    esp_http_client_config_t config = {
        .url = m->url,
        .timeout_ms = 15000,
    };
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) {
        return ESP_FAIL;
    }

    esp_err_t err = esp_http_client_open(client, 0);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "download open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        return err;
    }

    int64_t content_length = esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    if (status != 200) {
        ESP_LOGE(TAG, "firmware GET status %d", status);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "download avviato, content-length=%lld", (long long)content_length);

    esp_ota_handle_t ota_handle = 0;
    err = esp_ota_begin(update_partition, OTA_SIZE_UNKNOWN, &ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_begin failed: %s", esp_err_to_name(err));
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return err;
    }

    mbedtls_sha256_context sha_ctx;
    mbedtls_sha256_init(&sha_ctx);
    mbedtls_sha256_starts(&sha_ctx, 0 /* 0 = sha256, non sha224 */);

    uint8_t *buf = malloc(OTA_DOWNLOAD_CHUNK);
    if (!buf) {
        ESP_LOGE(TAG, "malloc buffer download fallita");
        mbedtls_sha256_free(&sha_ctx);
        esp_ota_abort(ota_handle);
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        return ESP_ERR_NO_MEM;
    }

    int total_written = 0;
    esp_err_t dl_err = ESP_OK;
    while (!esp_http_client_is_complete_data_received(client)) {
        int r = esp_http_client_read(client, (char *)buf, OTA_DOWNLOAD_CHUNK);
        if (r < 0) {
            ESP_LOGE(TAG, "errore lettura download a byte %d", total_written);
            dl_err = ESP_FAIL;
            break;
        }
        if (r == 0) {
            break;
        }
        err = esp_ota_write(ota_handle, buf, r);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "esp_ota_write failed: %s", esp_err_to_name(err));
            dl_err = err;
            break;
        }
        mbedtls_sha256_update(&sha_ctx, buf, r);
        total_written += r;
    }
    free(buf);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    if (dl_err != ESP_OK) {
        mbedtls_sha256_free(&sha_ctx);
        esp_ota_abort(ota_handle);
        return dl_err;
    }
    if (content_length > 0 && total_written != content_length) {
        ESP_LOGE(TAG, "download incompleto: %d/%lld byte", total_written, (long long)content_length);
        mbedtls_sha256_free(&sha_ctx);
        esp_ota_abort(ota_handle);
        return ESP_FAIL;
    }

    uint8_t digest[32];
    mbedtls_sha256_finish(&sha_ctx, digest);
    mbedtls_sha256_free(&sha_ctx);

    char digest_hex[65];
    sha256_to_hex(digest, digest_hex);
    ESP_LOGI(TAG, "download completo: %d byte, sha256=%s", total_written, digest_hex);

    if (strcasecmp(digest_hex, m->sha256) != 0) {
        ESP_LOGE(TAG, "sha256 mismatch! atteso=%s calcolato=%s -> OTA abortita", m->sha256, digest_hex);
        esp_ota_abort(ota_handle);
        return ESP_ERR_INVALID_CRC;
    }

    err = esp_ota_end(ota_handle);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_end failed: %s", esp_err_to_name(err));
        return err;
    }

    err = esp_ota_set_boot_partition(update_partition);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "esp_ota_set_boot_partition failed: %s", esp_err_to_name(err));
        return err;
    }

    ESP_LOGI(TAG, "OTA verificata e applicata (v%s), riavvio...", m->version);
    esp_restart();
    return ESP_OK; // mai raggiunto
}

esp_err_t ota_pull(const char *manifest_url)
{
    // Buffer del JSON (allocato da http_get_to_buffer) e struct manifest
    // allocati sull'HEAP: il task "main" ha uno stack limitato (~3.5KB di
    // default) e la catena esp_http_client + esp-tls + mbedtls usa già
    // parecchio stack da sola; array locali di queste dimensioni causavano
    // uno stack overflow su hardware reale (stessa classe di bug del
    // buffer audio nel Task 6).
    char *buf = NULL;
    int len = 0;
    int64_t content_length = 0;
    esp_err_t err = http_get_to_buffer(manifest_url, &buf, &len, &content_length);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "GET manifest fallito: %s", esp_err_to_name(err));
        return err; // buf resta NULL sugli errori di http_get_to_buffer
    }

    // Diagnostica per il round 3: il parse falliva in modo intermittente
    // per un manifest troncato/vuoto; questo log rende conclusivo il
    // prossimo test hardware (contenuto letto per intero vs atteso).
    ESP_LOGI(TAG, "manifest fetch: content_length=%d total=%d body=<%s>",
             (int)content_length, len, buf);

    ota_manifest_t *m = malloc(sizeof(ota_manifest_t));
    if (!m) {
        ESP_LOGE(TAG, "malloc manifest struct fallita");
        free(buf);
        return ESP_ERR_NO_MEM;
    }
    if (!ota_manifest_parse(buf, m)) {
        ESP_LOGW(TAG, "manifest non valido/non parsabile");
        free(buf);
        free(m);
        return ESP_ERR_INVALID_RESPONSE;
    }
    free(buf); // il JSON grezzo non serve più, il manifest è già parsato in m

    ESP_LOGI(TAG, "manifest: version=%s url=%s", m->version, m->url);

    if (!ota_should_update(fw_version(), m)) {
        ESP_LOGI(TAG, "nessun aggiornamento (corrente=%s manifest=%s)", fw_version(), m->version);
        free(m);
        return ESP_OK;
    }

    ESP_LOGI(TAG, "aggiornamento disponibile: %s -> %s", fw_version(), m->version);
    esp_err_t r = ota_download_and_apply(m);
    // Raggiunto solo sui path di errore: su successo ota_download_and_apply
    // chiama esp_restart() e non ritorna mai.
    free(m);
    return r;
}
