#include "buttons.h"
#include "button_policy.h"
#include "power.h"
#include "config.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define BUTTONS_MAX_CLICKS 8

static volatile int s_clicks;
static volatile uint32_t s_click_ms[BUTTONS_MAX_CLICKS];

static void watch_task(void *arg)
{
    (void)arg;
    click_det_t det;
    click_det_init(&det, power_pwr_pressed());
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(SB_BUTTON_SAMPLE_MS));
        if (click_det_step(&det, power_pwr_pressed()) && s_clicks < BUTTONS_MAX_CLICKS) {
            s_click_ms[s_clicks] = (uint32_t)(esp_timer_get_time() / 1000);
            s_clicks = s_clicks + 1;
        }
    }
}

esp_err_t buttons_watch_start(void)
{
    s_clicks = 0;
    return xTaskCreate(watch_task, "buttons", 2048, NULL, tskIDLE_PRIORITY + 3, NULL) == pdPASS
        ? ESP_OK : ESP_ERR_NO_MEM;
}

int buttons_clicks(void) { return s_clicks; }

uint32_t buttons_click_ms(int i)
{
    return (i >= 0 && i < s_clicks) ? s_click_ms[i] : 0;
}
