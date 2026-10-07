#include "unity.h"
#include "queue_policy.h"
#include "sync_policy.h"
#include "timesync_policy.h"
#include <string.h>

void test_queue_part_decide(void) {
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(0));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(44));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_DROP, queue_part_decide(44 + 32000 - 1));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_PROMOTE, queue_part_decide(44 + 32000));
    TEST_ASSERT_EQUAL_INT(QUEUE_PART_PROMOTE, queue_part_decide(19000000));
}

void test_sync_decide(void) {
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(200));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(201));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_DELETE, sync_decide(409));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(400));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(413));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(404));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_REJECT, sync_decide(422));
    // Token assente/sbagliato o device diverso dal token: non e' colpa del file,
    // la coda resta intatta e il sync si ferma (spec HTTPS+token §6).
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP_AUTH, sync_decide(401));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP_AUTH, sync_decide(403));
    // Rate limit (Cloudflare): temporaneo come un 5xx.
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(429));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(500));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(503));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(0));   // errore rete/timeout
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(-1));
    TEST_ASSERT_EQUAL_INT(SYNC_ACTION_STOP, sync_decide(302)); // redirect: non gestito -> stop
}

void test_http_status_is_auth_error(void) {
    TEST_ASSERT_TRUE(http_status_is_auth_error(401));
    TEST_ASSERT_TRUE(http_status_is_auth_error(403));
    TEST_ASSERT_FALSE(http_status_is_auth_error(429));
    TEST_ASSERT_FALSE(http_status_is_auth_error(400));
    TEST_ASSERT_FALSE(http_status_is_auth_error(200));
    TEST_ASSERT_FALSE(http_status_is_auth_error(0));
}

void test_timesync_needed(void) {
    const int64_t now = 1800000000;
    TEST_ASSERT_TRUE(timesync_needed(2000, now - 10, now));        // RTC non valido
    TEST_ASSERT_TRUE(timesync_needed(2026, 0, now));               // mai sincronizzato
    TEST_ASSERT_TRUE(timesync_needed(2026, now - 86401, now));     // piu' di 24 h
    TEST_ASSERT_FALSE(timesync_needed(2026, now - 3600, now));     // recente
    TEST_ASSERT_FALSE(timesync_needed(2026, now - 86400, now));    // esattamente 24 h: ok
}

void test_timesync_tm_to_epoch_utc(void) {
    struct tm t; memset(&t, 0, sizeof(t));
    t.tm_year = 1970 - 1900; t.tm_mon = 0; t.tm_mday = 1;
    TEST_ASSERT_EQUAL_INT64(0, timesync_tm_to_epoch_utc(&t));
    // 2026-09-23 19:15:30 UTC = 1790190930 (python: calendar.timegm)
    t.tm_year = 2026 - 1900; t.tm_mon = 8; t.tm_mday = 23; t.tm_hour = 19; t.tm_min = 15; t.tm_sec = 30;
    TEST_ASSERT_EQUAL_INT64(1790190930, timesync_tm_to_epoch_utc(&t));
    // 2000-02-29 (bisestile) 00:00:00 = 951782400
    memset(&t, 0, sizeof(t)); t.tm_year = 100; t.tm_mon = 1; t.tm_mday = 29;
    TEST_ASSERT_EQUAL_INT64(951782400, timesync_tm_to_epoch_utc(&t));
}
