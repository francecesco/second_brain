#include "queue_policy.h"
#include "wav.h"
#include "config.h"

queue_part_decision_t queue_part_decide(uint64_t file_size_bytes)
{
    // header + almeno SB_CAPTURE_MIN_MS di PCM
    const uint64_t min_bytes = WAV_HEADER_SIZE + ((uint64_t)WAV_BYTES_PER_SEC * SB_CAPTURE_MIN_MS) / 1000u;
    return file_size_bytes >= min_bytes ? QUEUE_PART_PROMOTE : QUEUE_PART_DROP;
}
