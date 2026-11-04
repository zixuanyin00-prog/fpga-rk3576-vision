#ifndef RGB_PREVIEW_SINK_H
#define RGB_PREVIEW_SINK_H
#include <stdint.h>
#include <stddef.h>
#include <stdatomic.h>
#include <semaphore.h>

enum {
    RGB_PREVIEW_FRAME_BYTES = 1920u * 1080u * 3u,
    RGB_PREVIEW_SINK_MAX_SLOTS = 16,
    RGB_PREVIEW_SINK_STATS_WORDS = 8
};

struct rgb_preview_sink {
    atomic_int enabled;
    uint32_t slots;
    uint8_t *rgb;
    sem_t *locks[RGB_PREVIEW_SINK_MAX_SLOTS];
    uint64_t *versions, *latest;
    uint32_t *sources, *requests;
    double *captured;
    uint64_t sequence;
    atomic_ullong published, dropped, last_source, last_request, last_received_ns;
};

void rgb_preview_sink_reset(struct rgb_preview_sink *sink);
int rgb_preview_sink_attach(struct rgb_preview_sink *sink, uint32_t slots, void *rgb,
                            uint64_t rgb_bytes, const uintptr_t *sem_handles, uint64_t *versions,
                            uint32_t *sources, uint32_t *requests, double *captured,
                            uint64_t *latest);

int rgb_preview_sink_publish(struct rgb_preview_sink *sink, const uint8_t *rgb,
                             const uint32_t meta[24], uint64_t received_ns);
void rgb_preview_sink_snapshot(struct rgb_preview_sink *sink, uint64_t out[8]);
#endif
