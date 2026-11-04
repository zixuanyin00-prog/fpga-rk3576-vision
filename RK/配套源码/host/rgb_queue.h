#ifndef RGB_QUEUE_H
#define RGB_QUEUE_H
#include <stdint.h>
#include <stddef.h>
struct jpeg_device;
enum { RGB_QUEUE_STATS_WORDS = 48, RGB_QUEUE_API_VERSION = 3 };

unsigned rgb_queue_api_version(void);
int rgb_queue_start(struct jpeg_device *);
int rgb_queue_start_configured(struct jpeg_device *, int cpu, int nice);
int rgb_device_copy_queued(struct jpeg_device *, unsigned, uint32_t, uint8_t *, size_t, uint32_t *);

int rgb_device_copy_queued_timed(struct jpeg_device *, unsigned, uint32_t, uint8_t *, size_t,
                                 uint32_t *, uint64_t *);
int rgb_queue_snapshot(struct jpeg_device *, uint64_t *, size_t);

int rgb_queue_set_preview_sink(struct jpeg_device *, uint32_t, void *, uint64_t, const uintptr_t *,
                               uint64_t *, uint32_t *, uint32_t *, double *, uint64_t *);
int rgb_queue_preview_sink_snapshot(struct jpeg_device *, uint64_t *, size_t);
int rgb_queue_stop(struct jpeg_device *);
#endif
