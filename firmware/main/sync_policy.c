#include "sync_policy.h"

sync_action_t sync_decide(int http_status)
{
    if (http_status == 200 || http_status == 201 || http_status == 409) return SYNC_ACTION_DELETE;
    if (http_status >= 400 && http_status < 500) return SYNC_ACTION_REJECT;
    return SYNC_ACTION_STOP;
}
