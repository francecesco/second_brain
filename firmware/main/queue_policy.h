#pragma once
#include <stdint.h>
// Decisione su un "<nome>.wav.part" trovato al boot (spec §5.5). Pura, testata su host.
typedef enum { QUEUE_PART_PROMOTE, QUEUE_PART_DROP } queue_part_decision_t;
queue_part_decision_t queue_part_decide(uint64_t file_size_bytes);
