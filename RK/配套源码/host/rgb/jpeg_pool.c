#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32)
#include <arm_acle.h>
#endif
#include "jpeg_pool.h"
#include <string.h>

static void acquire_pool(void) {
#if defined(__aarch64__)
    __asm__ volatile("dmb oshld" ::: "memory");
#else
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
#endif
}
static uint32_t crc_bytes(const uint8_t *data, size_t n) {
    uint32_t c = 0xffffffffu;
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32)
    while (n >= 8) {
        uint64_t v;
        memcpy(&v, data, 8);
        c = __crc32d(c, v);
        data += 8;
        n -= 8;
    }
    while (n--)
        c = __crc32b(c, *data++);
#else
    while (n--) {
        c ^= *data++;
        for (unsigned k = 0; k < 8; k++)
            c = (c >> 1) ^ ((c & 1) ? 0xedb88320u : 0u);
    }
#endif
    return ~c;
}
static int newer(uint32_t a, uint32_t b) {
    uint32_t delta = a - b;
    return delta != 0 && delta < 0x80000000u;
}
int jpeg_copy_ring(const volatile uint8_t *pool, size_t pool_size, uint32_t last, uint8_t *out,
                   size_t cap, uint32_t meta[24], uint32_t w, uint32_t h, uint32_t stride,
                   uint32_t offset) {
    if (!pool || !out || !meta ||
        ((pool_size != stride && pool_size != 2u * stride) || stride < offset + 96u) ||
        ((uintptr_t)pool & 3))
        return -4;
    int slot = -1;
    uint32_t selected = last;
    for (unsigned s = 0; s < pool_size / stride; s++) {
        const volatile uint32_t *m = (const volatile uint32_t *)(pool + s * stride + offset);
        uint32_t id = m[15];
        acquire_pool();
        if (id && newer(id, last) && (slot < 0 || newer(selected, id)) && m[1] == id) {
            selected = id;
            slot = (int)s;
        }
    }
    if (slot < 0)
        return 0;
    const volatile uint32_t *m =
        (const volatile uint32_t *)(pool + (unsigned)slot * stride + offset);
    acquire_pool();
    for (unsigned k = 0; k < JPEG_META_WORDS; k++)
        meta[k] = m[k];
    if (meta[15] != selected || meta[1] != selected)
        return 0;

    if (!meta[0])
        return 0;
    int raw_preview = stride == RGB_RAW_PREVIEW_STRIDE && offset == RGB_RAW_PREVIEW_META &&
                      w == 1920 && h == 1080;
    unsigned max_bytes = raw_preview ? JPEG_MAX_BYTES : JPEG_ROI_BYTES;
    if (meta[0] != 0x32434441u || (meta[9] & 3) != (raw_preview ? 1u : 3u) || meta[10] != 8 ||
        meta[11] != 0x33424752u || meta[2] == 0 || meta[2] > max_bytes ||
        meta[14] != ((meta[2] + 3u) & ~3u) || (w && meta[12] != w) || (h && meta[13] != h) ||
        !meta[12] || !meta[13] || meta[12] > 1920 || meta[13] > 1080)
        return -3;
    w = meta[12];
    h = meta[13];
    unsigned x = meta[8] & 4095, y = (meta[8] >> 12) & 4095, channel = (meta[9] >> 8) & 255;
    if (meta[2] != w * h * 3 || (!raw_preview && w * h > JPEG_MAX_ROI_PIXELS) || !meta[4] ||
        !meta[5] || (meta[8] >> 24) || (meta[9] & ~0xff03u) ||
        (raw_preview ? (channel != 0 || x || y) : (channel < 1 || channel > 2)) || x + w > 1920 ||
        y + h > 1080 || !meta[18] || meta[19] != meta[4] || meta[20] != 0x38504d52u || meta[21] ||
        meta[22] || meta[23])
        return -3;
    if (cap < meta[2])
        return -4;
    const volatile uint8_t *p = pool + (unsigned)slot * stride;
    memcpy(out, (const void *)p, meta[2]);
    int pad_bad = 0;
    for (unsigned k = meta[2]; k < meta[14]; k++)
        if (p[k])
            pad_bad = 1;
    acquire_pool();
    for (unsigned k = 0; k < JPEG_META_WORDS; k++)
        if (m[k] != meta[k])
            return 0;
    if (pad_bad)
        return -3;
    return crc_bytes(out, meta[2]) == meta[3] ? 1 : -2;
}

int jpeg_copy_pool(const volatile uint8_t *pool, size_t pool_size, uint32_t last, uint8_t *out,
                   size_t cap, uint32_t meta[24], uint32_t w, uint32_t h) {
    if (pool_size != JPEG_POOL_BYTES)
        return -4;
    if (w != 1920 || h != 1080)
        return -4;
    return jpeg_copy_ring(pool, 2u * RGB_RAW_PREVIEW_STRIDE, last, out, cap, meta, w, h,
                          RGB_RAW_PREVIEW_STRIDE, RGB_RAW_PREVIEW_META);
}
