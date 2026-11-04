
#define _GNU_SOURCE
#include "rgb_queue.h"
#include "preview_sink.h"
#include "preview_decode.h"
#include "preview_geometry.h"
#include "rgb/jpeg_pool.h"
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <stdatomic.h>
#include <time.h>
#include <errno.h>
#include <sched.h>
#include <sys/resource.h>
#include <sys/syscall.h>
#include <unistd.h>
#define CAP 6220800u
#define DEPTH 16u
#define ROI_DEPTH 2u
#define ROI_FIFO_DEPTH 4u
extern int rgb_device_copy_channel(struct jpeg_device *, unsigned, uint32_t, uint8_t *, size_t,
                                   uint32_t *);
struct packet {
    uint32_t meta[24];
    uint8_t *bytes;
    uint64_t received_ns;
};
struct ring {
    struct packet *frames;
    unsigned begin, count, high_water, capacity;
    uint64_t overflow;
};
static struct {
    struct jpeg_device *device;
    pthread_t thread[3];
    pthread_mutex_t mutex[3];
    atomic_int stop, error, ready[3], setup_error;
    atomic_ullong max_poll_gap_ns;
    int cpu, nice, roi_fifo, tid[3], policy[3];
    struct ring rings[3];
    uint64_t frames[3], descriptor_gaps[3], preview_source_gaps;
    uint64_t copy_ns[3], copy_max_ns[3];
    uint64_t compressed_bytes, decode_ns, decode_max_ns;
    uint32_t compression_format;
    uint32_t last_source[3], last_seq[3];
    uint64_t latest_skipped[3], last_received_ns[3], last_dequeued_ns[3];
    uint32_t last_skipped_source[3], last_dequeued_source[3];
    struct rgb_preview_sink preview_sink;
} q;
static uint64_t monotonic_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000000ull + (uint64_t)t.tv_nsec;
}
static int configure_reader(unsigned channel) {
    cpu_set_t mask;
    CPU_ZERO(&mask);
    int cpu = channel ? 3 : q.cpu;
    int nice = channel ? 0 : q.nice;
    CPU_SET(cpu, &mask);
    int rc = pthread_setaffinity_np(pthread_self(), sizeof(mask), &mask);
    if (rc)
        return rc;
    struct sched_param param = {.sched_priority = channel ? 0 : 10};
    int expected_policy = channel ? SCHED_OTHER : SCHED_FIFO;
    if ((rc = pthread_setschedparam(pthread_self(), expected_policy, &param)))
        return rc;
    if (setpriority(PRIO_PROCESS, 0, nice))
        return errno;
    errno = 0;
    int actual_nice = getpriority(PRIO_PROCESS, 0);
    if (errno)
        return errno;
    CPU_ZERO(&mask);
    if ((rc = pthread_getaffinity_np(pthread_self(), sizeof(mask), &mask)))
        return rc;
    if ((rc = pthread_getschedparam(pthread_self(), &q.policy[channel], &param)))
        return rc;
    if (CPU_COUNT(&mask) != 1 || !CPU_ISSET(cpu, &mask) || actual_nice != nice ||
        q.policy[channel] != expected_policy)
        return EPROTO;
    q.tid[channel] = (int)syscall(SYS_gettid);
    pthread_setname_np(pthread_self(), channel == 2 ? "rgb-web-reader"
                                       : channel    ? "rgb-roi-reader"
                                                    : "rgb-dma-reader");
    return 0;
}
static void *drain(void *unused) {
    unsigned channel = (unsigned)(uintptr_t)unused;
    int rc = configure_reader(channel);
    struct packet *temp = NULL;
    if (!rc) {
        temp = malloc(sizeof(*temp));
        if (!temp)
            rc = ENOMEM;
        else {
            temp->bytes = malloc(channel ? JPEG_ROI_BYTES : PREVIEW_WIRE_CAP);
            if (!temp->bytes) {
                free(temp);
                temp = NULL;
                rc = ENOMEM;
            }
        }
    }
    if (rc) {
        atomic_store(&q.setup_error, rc);
        atomic_store(&q.ready[channel], -1);
        return NULL;
    }
    uint32_t last = 0;
    uint64_t previous_poll = monotonic_ns();
    atomic_store(&q.ready[channel], 1);
    while (!atomic_load(&q.stop)) {
        uint64_t now = monotonic_ns(), gap = now - previous_poll;
        previous_poll = now;
        if (channel == 0 && gap > atomic_load(&q.max_poll_gap_ns))
            atomic_store(&q.max_poll_gap_ns, gap);
        int progress = 0;
        {
            uint64_t copy_started = monotonic_ns();
            rc = rgb_device_copy_channel(q.device, channel, last, temp->bytes,
                                         channel ? JPEG_ROI_BYTES : PREVIEW_WIRE_CAP, temp->meta);
            uint64_t copy_completed = monotonic_ns();
            uint64_t copy_elapsed = copy_completed - copy_started;
            temp->received_ns = copy_completed;
            if (rc < 0) {
                atomic_store(&q.error, rc);
                atomic_store(&q.stop, 1);
                break;
            }
            if (!rc) {
                struct timespec pause = {0, channel == 2 ? 1000000 : 100000};
                nanosleep(&pause, NULL);
                continue;
            }
            uint32_t seq = temp->meta[1], source = temp->meta[5];
            progress = 1;
            uint32_t wire_format = temp->meta[11];
            if (wire_format != 0x33424752u) {
                atomic_store(&q.error, -EOPNOTSUPP);
                atomic_store(&q.stop, 1);
                break;
            }
            pthread_mutex_lock(&q.mutex[channel]);
            struct ring *r = &q.rings[channel];
            if (q.frames[channel]) {
                uint32_t delta = seq - last;
                if (!delta || delta >= 0x80000000u) {
                    pthread_mutex_unlock(&q.mutex[channel]);
                    atomic_store(&q.error, -5);
                    atomic_store(&q.stop, 1);
                    break;
                }
                q.descriptor_gaps[channel] += delta - 1;
                if (channel == 0) {
                    delta = source - q.last_source[0];
                    if (!delta || delta >= 0x80000000u) {
                        pthread_mutex_unlock(&q.mutex[channel]);
                        atomic_store(&q.error, -5);
                        atomic_store(&q.stop, 1);
                        break;
                    }
                    q.preview_source_gaps += delta - 1;
                }
            }
            q.copy_ns[channel] += copy_elapsed;
            if (copy_elapsed > q.copy_max_ns[channel])
                q.copy_max_ns[channel] = copy_elapsed;
            if (!channel) {
                q.compressed_bytes += temp->meta[2];
                q.compression_format = wire_format;
            }
            last = seq;
            q.last_seq[channel] = seq;
            q.last_source[channel] = source;
            q.frames[channel]++;
            q.last_received_ns[channel] = temp->received_ns;
            if (r->count == r->capacity) {
                r->begin = (r->begin + 1) % r->capacity;
                r->count--;
                r->overflow++;
            }
            struct packet *target = &r->frames[(r->begin + r->count) % r->capacity];
            target->received_ns = temp->received_ns;
            memcpy(target->meta, temp->meta, sizeof(temp->meta));
            memcpy(target->bytes, temp->bytes, temp->meta[2]);
            r->count++;
            if (r->count > r->high_water)
                r->high_water = r->count;
            if (channel == 0)
                rgb_preview_sink_publish(&q.preview_sink, temp->bytes, temp->meta,
                                         temp->received_ns);
            pthread_mutex_unlock(&q.mutex[channel]);
        }
        if (!progress) {
            struct timespec pause = {0, 100000};
            nanosleep(&pause, NULL);
        }
    }
    free(temp->bytes);
    free(temp);
    return NULL;
}
static void release_queue(void) {
    for (unsigned i = 0; i < 3; i++)
        if (q.rings[i].frames) {
            for (unsigned j = 0; j < q.rings[i].capacity; j++)
                free(q.rings[i].frames[j].bytes);
            free(q.rings[i].frames);
        }
    for (unsigned channel = 0; channel < 3; channel++)
        pthread_mutex_destroy(&q.mutex[channel]);
    q.device = NULL;
}
unsigned rgb_queue_api_version(void) {
    return RGB_QUEUE_API_VERSION;
}
int rgb_queue_start_configured(struct jpeg_device *device, int cpu, int nice) {
    if (q.device) {
        errno = EBUSY;
        return -1;
    }
    if (!device || cpu < 0 || cpu >= CPU_SETSIZE || nice < -20 || nice > 19) {
        errno = EINVAL;
        return -1;
    }
    memset(&q, 0, sizeof(q));
    q.device = device;
    q.cpu = cpu;
    q.nice = nice;
    const char *minimal = getenv("VISION_MINIMAL60");
    q.roi_fifo = minimal && !strcmp(minimal, "1");
    rgb_preview_sink_reset(&q.preview_sink);
    atomic_init(&q.stop, 0);
    atomic_init(&q.error, 0);
    for (unsigned channel = 0; channel < 3; channel++)
        atomic_init(&q.ready[channel], 0);
    atomic_init(&q.setup_error, 0);
    atomic_init(&q.max_poll_gap_ns, 0);
    int rc = 0;
    for (unsigned i = 0; i < 3; i++)
        if ((rc = pthread_mutex_init(&q.mutex[i], NULL))) {
            while (i)
                pthread_mutex_destroy(&q.mutex[--i]);
            q.device = NULL;
            errno = rc;
            return -1;
        }
    for (unsigned i = 0; i < 3; i++)
        q.rings[i].capacity = i == 1 && q.roi_fifo ? ROI_FIFO_DEPTH : (i ? ROI_DEPTH : DEPTH);
    for (unsigned i = 0; i < 3; i++)
        if (!(q.rings[i].frames = calloc(q.rings[i].capacity, sizeof(struct packet)))) {
            release_queue();
            errno = ENOMEM;
            return -1;
        }
    for (unsigned i = 0; i < 3; i++)
        for (unsigned j = 0; j < q.rings[i].capacity; j++)
            if (!(q.rings[i].frames[j].bytes = malloc(i ? JPEG_ROI_BYTES : CAP))) {
                release_queue();
                errno = ENOMEM;
                return -1;
            }
    unsigned created = 0;
    for (unsigned channel = 0; channel < 3; channel++) {
        rc = pthread_create(&q.thread[channel], NULL, drain, (void *)(uintptr_t)channel);
        if (rc)
            break;
        created++;
    }
    uint64_t deadline = monotonic_ns() + 2000000000ull;
    while (!rc &&
           (!atomic_load(&q.ready[0]) || !atomic_load(&q.ready[1]) || !atomic_load(&q.ready[2])) &&
           monotonic_ns() < deadline) {
        struct timespec pause = {0, 100000};
        nanosleep(&pause, NULL);
    }
    if (rc || atomic_load(&q.ready[0]) != 1 || atomic_load(&q.ready[1]) != 1 ||
        atomic_load(&q.ready[2]) != 1) {
        if (!rc)
            rc = atomic_load(&q.setup_error);
        if (!rc)
            rc = ETIMEDOUT;
        atomic_store(&q.stop, 1);
        for (unsigned i = 0; i < created; i++)
            pthread_join(q.thread[i], NULL);
        release_queue();
        errno = rc;
        return -1;
    }
    return 0;
}

int rgb_queue_start(struct jpeg_device *device) {
    return rgb_queue_start_configured(device, 6, -20);
}
int rgb_device_copy_queued_timed(struct jpeg_device *device, unsigned channel, uint32_t last,
                                 uint8_t *out, size_t capacity, uint32_t *meta, uint64_t *timing) {
    (void)last;
    if (q.device != device || !device || channel > 2 ||
        capacity < (channel ? JPEG_ROI_BYTES : CAP) || !out || !meta)
        return -4;
    int error = atomic_load(&q.error);
    if (error)
        return error;
    pthread_mutex_lock(&q.mutex[channel]);
    struct ring *r = &q.rings[channel];
    if (!r->count) {
        pthread_mutex_unlock(&q.mutex[channel]);
        return 0;
    }
    if ((channel == 2 || (channel == 1 && !q.roi_fifo) ||
         (channel == 0 &&
          atomic_load_explicit(&q.preview_sink.enabled, memory_order_acquire) == 1)) &&
        r->count > 1) {
        unsigned skipped = r->count - 1;
        q.latest_skipped[channel] += skipped;
        q.last_skipped_source[channel] = r->frames[(r->begin + skipped - 1) % r->capacity].meta[5];
        r->begin = (r->begin + skipped) % r->capacity;
        r->count = 1;
    }
    struct packet *p = &r->frames[r->begin];
    memcpy(out, p->bytes, p->meta[2]);
    memcpy(meta, p->meta, sizeof(p->meta));
    uint64_t dequeued = monotonic_ns();
    q.last_dequeued_ns[channel] = dequeued;
    q.last_dequeued_source[channel] = p->meta[5];
    if (timing) {
        timing[0] = p->received_ns;
        timing[1] = dequeued;
    }
    r->begin = (r->begin + 1) % r->capacity;
    r->count--;
    pthread_mutex_unlock(&q.mutex[channel]);
    return 1;
}
int rgb_device_copy_queued(struct jpeg_device *device, unsigned channel, uint32_t last,
                           uint8_t *out, size_t capacity, uint32_t *meta) {
    return rgb_device_copy_queued_timed(device, channel, last, out, capacity, meta, NULL);
}
int rgb_queue_snapshot(struct jpeg_device *device, uint64_t *v, size_t count) {
    if (q.device != device || !device || !v || count < RGB_QUEUE_STATS_WORDS) {
        errno = EINVAL;
        return -1;
    }
    cpu_set_t mask;
    CPU_ZERO(&mask);
    if (sched_getaffinity(q.tid[0], sizeof(mask), &mask))
        return -1;
    errno = 0;
    int actual_nice = getpriority(PRIO_PROCESS, q.tid[0]);
    if (errno)
        return -1;
    int policy = sched_getscheduler(q.tid[0]);
    if (policy < 0)
        return -1;
    memset(v, 0, RGB_QUEUE_STATS_WORDS * sizeof(*v));
    v[0] = RGB_QUEUE_API_VERSION;
    v[1] = (uint64_t)q.tid[0];
    v[2] = (uint64_t)q.cpu;
    v[3] = (uint64_t)(actual_nice + 20);
    v[4] = (uint64_t)policy;
    for (unsigned i = 0; i < 2; i++) {
        pthread_mutex_lock(&q.mutex[i]);
        v[5 + i] = q.frames[i];
        v[7 + i] = q.descriptor_gaps[i];
        v[10 + i] = q.rings[i].overflow;
        v[12 + i] = q.rings[i].count;
        v[14 + i] = q.rings[i].high_water;
        v[17 + i] = q.last_source[i];
        v[19 + i] = q.last_seq[i];
        if (i == 0)
            v[9] = q.preview_source_gaps;
        else {
            v[24] = q.latest_skipped[1];
            v[25] = q.last_skipped_source[1];
            v[26] = q.last_received_ns[1];
            v[27] = q.last_dequeued_ns[1];
            v[28] = q.last_dequeued_source[1];
            v[29] = q.rings[1].capacity;
        }
        v[30] = q.rings[0].capacity;
        v[31] = 1;
        pthread_mutex_unlock(&q.mutex[i]);
    }
    v[16] = atomic_load(&q.max_poll_gap_ns);
    v[23] = (uint64_t)q.tid[1];
    pthread_mutex_lock(&q.mutex[2]);
    v[32] = (uint64_t)q.tid[2];
    v[33] = q.frames[2];
    v[34] = q.descriptor_gaps[2];
    v[35] = q.rings[2].overflow;
    v[36] = q.rings[2].count;
    v[37] = q.rings[2].high_water;
    v[38] = q.last_source[2];
    v[39] = q.last_seq[2];
    v[40] = q.latest_skipped[2];
    v[41] = q.last_skipped_source[2];
    v[42] = q.last_received_ns[2];
    v[43] = q.last_dequeued_ns[2];
    v[44] = q.last_dequeued_source[2];
    v[45] = q.rings[2].capacity;
    v[46] = q.copy_ns[2];
    v[47] = q.copy_max_ns[2];
    pthread_mutex_unlock(&q.mutex[2]);
    int error = atomic_load(&q.error);
    v[21] = (uint64_t)(error < 0 ? -error : error);
    v[22] = (CPU_COUNT(&mask) == 1 && CPU_ISSET(q.cpu, &mask) && actual_nice == q.nice &&
             policy == SCHED_FIFO);
    return 0;
}
int rgb_queue_set_preview_sink(struct jpeg_device *device, uint32_t slots, void *rgb,
                               uint64_t rgb_bytes, const uintptr_t *locks, uint64_t *versions,
                               uint32_t *sources, uint32_t *requests, double *captured,
                               uint64_t *latest) {
    if (q.device != device || !device || atomic_load(&q.stop)) {
        errno = EINVAL;
        return -1;
    }
    pthread_mutex_lock(&q.mutex[0]);
    int rc = rgb_preview_sink_attach(&q.preview_sink, slots, rgb, rgb_bytes, locks, versions,
                                     sources, requests, captured, latest);
    pthread_mutex_unlock(&q.mutex[0]);
    return rc;
}
int rgb_queue_preview_sink_snapshot(struct jpeg_device *device, uint64_t *out, size_t count) {
    if (q.device != device || !device || !out || count < RGB_PREVIEW_SINK_STATS_WORDS) {
        errno = EINVAL;
        return -1;
    }
    pthread_mutex_lock(&q.mutex[0]);
    rgb_preview_sink_snapshot(&q.preview_sink, out);
    if (count >= 10) {
        out[8] = q.latest_skipped[0];
        out[9] = q.last_skipped_source[0];
    }
    pthread_mutex_unlock(&q.mutex[0]);
    return 0;
}
int rgb_queue_stop(struct jpeg_device *device) {
    if (q.device != device || !device)
        return -1;
    atomic_store(&q.stop, 1);
    for (unsigned i = 0; i < 3; i++)
        pthread_join(q.thread[i], NULL);
    release_queue();
    return 0;
}

int preview_decode_snapshot(struct jpeg_device *device, uint64_t *v, size_t count) {
    if (q.device != device || !device || !v || count < 9)
        return -1;
    pthread_mutex_lock(&q.mutex[0]);
    v[0] = q.frames[0];
    v[1] = q.compressed_bytes;
    v[2] = q.decode_ns;
    v[3] = q.decode_max_ns;
    v[4] = q.compression_format;
    v[5] = q.copy_ns[0];
    v[6] = q.copy_max_ns[0];
    v[7] = q.copy_ns[1];
    v[8] = q.copy_max_ns[1];
    pthread_mutex_unlock(&q.mutex[0]);
    return 0;
}
