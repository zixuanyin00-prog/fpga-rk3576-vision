#ifndef JPEG_POOL_H
#define JPEG_POOL_H
#include <stdint.h>
#include <stddef.h>
#define JPEG_SLOT_STRIDE 0x200000u
#define JPEG_META_OFFSET 0x1ff000u
#define JPEG_POOL_BYTES 0x1000000u
#define RGB_RAW_PREVIEW_STRIDE 0x600000u
#define RGB_RAW_PREVIEW_META 0x5ff000u
#define JPEG_ROI_BASE 0xc00000u
#define JPEG_CAPTURE_BASE 0xe00000u
#define JPEG_MAX_ROI_PIXELS 518400u
#define JPEG_ROI_BYTES (JPEG_MAX_ROI_PIXELS * 3u)
#define JPEG_META_WORDS 24u
#define JPEG_MAX_BYTES 6220800u

int jpeg_copy_ring(const volatile uint8_t *pool, size_t pool_size, uint32_t last, uint8_t *out,
                   size_t cap, uint32_t meta[24], uint32_t w, uint32_t h, uint32_t stride,
                   uint32_t offset);
int jpeg_copy_pool(const volatile uint8_t *pool, size_t pool_size, uint32_t last, uint8_t *output,
                   size_t capacity, uint32_t metadata[24], uint32_t width, uint32_t height);
#endif
