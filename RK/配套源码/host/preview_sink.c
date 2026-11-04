#include "preview_sink.h"
#include <errno.h>
#include <string.h>

void rgb_preview_sink_reset(struct rgb_preview_sink *s) {
    memset(s, 0, sizeof(*s));
    atomic_init(&s->enabled, 0);
    atomic_init(&s->published, 0);
    atomic_init(&s->dropped, 0);
    atomic_init(&s->last_source, 0);
    atomic_init(&s->last_request, 0);
    atomic_init(&s->last_received_ns, 0);
}

int rgb_preview_sink_attach(struct rgb_preview_sink *s, uint32_t slots, void *rgb,
                            uint64_t rgb_bytes, const uintptr_t *sem_handles, uint64_t *versions,
                            uint32_t *sources, uint32_t *requests, double *captured,
                            uint64_t *latest) {
    if (!s || !rgb || !sem_handles || !versions || !sources || !requests || !captured || !latest ||
        slots < 2 || slots > RGB_PREVIEW_SINK_MAX_SLOTS ||
        rgb_bytes != (uint64_t)slots * RGB_PREVIEW_FRAME_BYTES ||
        (uintptr_t)versions % _Alignof(uint64_t) || (uintptr_t)sources % _Alignof(uint32_t) ||
        (uintptr_t)requests % _Alignof(uint32_t) || (uintptr_t)captured % _Alignof(double) ||
        (uintptr_t)latest % _Alignof(uint64_t)) {
        errno = EINVAL;
        return -1;
    }
    for (uint32_t i = 0; i < slots; i++)
        if (!sem_handles[i] || sem_handles[i] % _Alignof(sem_t)) {
            errno = EINVAL;
            return -1;
        }
    int expected = 0;
    if (!atomic_compare_exchange_strong(&s->enabled, &expected, -1)) {
        errno = EBUSY;
        return -1;
    }
    s->slots = slots;
    s->rgb = rgb;
    s->versions = versions;
    s->sources = sources;
    s->requests = requests;
    s->captured = captured;
    s->latest = latest;
    s->sequence = 0;
    for (uint32_t i = 0; i < slots; i++) {
        s->locks[i] = (sem_t *)sem_handles[i];
        if (versions[i] > s->sequence)
            s->sequence = versions[i];
    }
    atomic_store_explicit(&s->enabled, 1, memory_order_release);
    return 0;
}

int rgb_preview_sink_publish(struct rgb_preview_sink *s, const uint8_t *rgb,
                             const uint32_t meta[24], uint64_t received_ns) {
    if (atomic_load_explicit(&s->enabled, memory_order_acquire) != 1)
        return 0;
    uint32_t tried = 0;
    for (uint32_t attempt = 0; attempt < s->slots; attempt++) {
        uint32_t slot = s->slots;
        uint64_t oldest = UINT64_MAX;
        for (uint32_t i = 0; i < s->slots; i++)
            if (!(tried & (1u << i)) && s->versions[i] < oldest) {
                oldest = s->versions[i];
                slot = i;
            }
        if (slot == s->slots)
            break;
        tried |= 1u << slot;
        if (sem_trywait(s->locks[slot]))
            continue;
        memcpy(s->rgb + (size_t)slot * RGB_PREVIEW_FRAME_BYTES, rgb, RGB_PREVIEW_FRAME_BYTES);
        s->sources[slot] = meta[5];
        s->requests[slot] = meta[4];
        s->captured[slot] = (double)received_ns / 1000000000.0;
        s->versions[slot] = ++s->sequence;
        __atomic_store_n(s->latest, s->sequence * s->slots + slot, __ATOMIC_RELEASE);
        sem_post(s->locks[slot]);
        atomic_store(&s->last_source, meta[5]);
        atomic_store(&s->last_request, meta[4]);
        atomic_store(&s->last_received_ns, received_ns);
        atomic_fetch_add(&s->published, 1);
        return 1;
    }
    atomic_fetch_add(&s->dropped, 1);
    return 0;
}

void rgb_preview_sink_snapshot(struct rgb_preview_sink *s, uint64_t out[8]) {
    int enabled = atomic_load_explicit(&s->enabled, memory_order_acquire);
    out[0] = enabled == 1;
    out[1] = atomic_load(&s->published);
    out[2] = atomic_load(&s->dropped);
    out[3] = atomic_load(&s->last_source);
    out[4] = atomic_load(&s->last_request);
    out[5] = enabled == 1 ? s->sequence : 0;
    out[6] = atomic_load(&s->last_received_ns);
    out[7] = enabled == 1 ? s->slots : 0;
}
