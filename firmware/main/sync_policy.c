#include "sync_policy.h"

bool http_status_is_auth_error(int http_status)
{
    return http_status == 401 || http_status == 403;
}

sync_action_t sync_decide(int http_status)
{
    if (http_status == 200 || http_status == 201 || http_status == 409) return SYNC_ACTION_DELETE;
    if (http_status_is_auth_error(http_status)) return SYNC_ACTION_STOP_AUTH;
    if (http_status == 429) return SYNC_ACTION_STOP;
    if (http_status >= 400 && http_status < 500) return SYNC_ACTION_REJECT;
    return SYNC_ACTION_STOP;
}
