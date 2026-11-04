
#include "preview_decode.h"
#include <string.h>
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32)
#include <arm_acle.h>
#endif

uint32_t preview_rgb_crc(const uint8_t *data, size_t bytes) {
    uint32_t crc = 0xffffffffu;
#if defined(__aarch64__) && defined(__ARM_FEATURE_CRC32)
    while (bytes >= 8) {
        uint64_t word;
        memcpy(&word, data, 8);
        crc = __crc32d(crc, word);
        data += 8;
        bytes -= 8;
    }
    while (bytes--)
        crc = __crc32b(crc, *data++);
#else
    while (bytes--) {
        crc ^= *data++;
        for (unsigned k = 0; k < 8; k++)
            crc = (crc >> 1) ^ ((crc & 1) ? 0xedb88320u : 0u);
    }
#endif
    return ~crc;
}
