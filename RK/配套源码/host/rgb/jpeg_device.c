#define _GNU_SOURCE
#define _POSIX_C_SOURCE 200809L
#include "jpeg_device.h"
#include "jpeg_pool.h"
#include "../preview_geometry.h"
#include "../preview_decode.h"
#include "../../driver/camera_dma_uapi.h"
#include <sys/mman.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <stdlib.h>
#include <errno.h>
#include <string.h>
struct jpeg_device {
    int fd, started;
    uint32_t width, height;
    volatile uint8_t *pool;
};

static void *map_chunked_pool(int fd) {
    uint8_t *base = mmap(NULL, JPEG_POOL_BYTES, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (base == MAP_FAILED)
        return MAP_FAILED;
    for (unsigned i = 0; i < CAM_CHUNK_COUNT; i++) {
        void *wanted = base + (size_t)i * CAM_CHUNK_BYTES;
        void *actual = mmap(wanted, CAM_CHUNK_BYTES, PROT_READ, MAP_SHARED | MAP_FIXED, fd,
                            (off_t)i * CAM_CHUNK_BYTES);
        if (actual == MAP_FAILED) {
            int saved = errno;
            munmap(base, JPEG_POOL_BYTES);
            errno = saved;
            return MAP_FAILED;
        }
    }
    return base;
}
struct jpeg_device *jpeg_device_open(const char *path) {
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0)
        return NULL;
    uint32_t mode = 0;
    if (ioctl(fd, CAM_SET_MODE, &mode) < 0) {
        close(fd);
        return NULL;
    }
    struct cam_info info;
    if (ioctl(fd, CAM_GET_INFO, &info) < 0) {
        close(fd);
        return NULL;
    }
    if ((info.version != CAM_VERSION) || info.width != 1920 || info.height != 1080 ||
        info.frame_bytes != JPEG_MAX_BYTES || info.slots != CAM_SLOT_COUNT ||
        info.stride != JPEG_SLOT_STRIDE || info.meta_offset != JPEG_META_OFFSET ||
        info.pool_bytes != JPEG_POOL_BYTES) {
        close(fd);
        errno = EPROTO;
        return NULL;
    }
    const char *codec = getenv("VISION_PREVIEW_CODEC");
    uint32_t compression = CAM_PREVIEW_RAW;
    if (codec && strcmp(codec, "raw")) {
        close(fd);
        errno = EOPNOTSUPP;
        return NULL;
    }
    if (ioctl(fd, CAM_PREVIEW_COMPRESSION, &compression) < 0) {
        close(fd);
        return NULL;
    }
    void *pool = map_chunked_pool(fd);
    if (pool == MAP_FAILED) {
        close(fd);
        return NULL;
    }
    struct jpeg_device *d = calloc(1, sizeof(*d));
    if (!d) {
        munmap(pool, JPEG_POOL_BYTES);
        close(fd);
        return NULL;
    }
    d->fd = fd;
    d->pool = pool;
    d->width = info.width;
    d->height = info.height;
    return d;
}
int jpeg_device_start(struct jpeg_device *d, unsigned gaussian, unsigned equalize) {
    if (!d || gaussian || equalize) {
        errno = EINVAL;
        return -1;
    }
    if (d->started) {
        errno = EBUSY;
        return -1;
    }
    struct cam_filters filters = {gaussian, equalize};
    if (ioctl(d->fd, CAM_SET_FILTERS, &filters) < 0)
        return -1;
    if (ioctl(d->fd, CAM_START) < 0)
        return -1;
    d->started = 1;
    return 0;
}
int jpeg_device_copy(struct jpeg_device *d, uint32_t last, uint8_t *out, size_t cap,
                     uint32_t meta[24]) {
    if (!d || !d->started)
        return -4;
    return rgb_device_copy_channel(d, 0, last, out, cap, meta);
}
int jpeg_device_status(struct jpeg_device *d, uint32_t status[4]) {
    if (!d || !status) {
        errno = EINVAL;
        return -1;
    }
    struct cam_status s;
    if (ioctl(d->fd, CAM_GET_STATUS, &s) < 0)
        return -1;
    status[0] = s.status;
    status[1] = s.last_frame;
    status[2] = s.bad_config;
    status[3] = s.protocol;
    return 0;
}
int jpeg_device_close(struct jpeg_device *d) {
    if (!d)
        return 0;
    int ret = d->started ? ioctl(d->fd, CAM_STOP) : 0;

    munmap((void *)d->pool, JPEG_POOL_BYTES);
    close(d->fd);
    free(d);
    return ret;
}

int rgb_device_request_rect(struct jpeg_device *d, unsigned id, unsigned x, unsigned y, unsigned w,
                            unsigned h) {
    return rgb_device_request_rect_channel(d, 1, id, x, y, w, h);
}
int rgb_device_request_rect_channel(struct jpeg_device *d, unsigned channel, unsigned id,
                                    unsigned x, unsigned y, unsigned w, unsigned h) {
    if (!d || !d->started) {
        errno = EINVAL;
        return -1;
    }
    struct cam_rect command = {id, x, y, w, h, channel};
    return ioctl(d->fd, CAM_REQUEST_RECT, &command);
}
int rgb_device_request(struct jpeg_device *d, unsigned request_id, unsigned roi, unsigned x,
                       unsigned y) {
    if (!d || !d->started) {
        errno = EINVAL;
        return -1;
    }
    struct cam_roi command = {request_id, roi, x, y};
    return ioctl(d->fd, CAM_REQUEST_ROI, &command);
}

int rgb_device_features(struct jpeg_device *d, struct cam_features *features) {
    return ioctl(d->fd, CAM_GET_FEATURES, features);
}
int rgb_device_feature_config(struct jpeg_device *d, unsigned config) {
    return ioctl(d->fd, CAM_SET_FEATURES, &config);
}

int rgb_device_industrial(struct jpeg_device *d, struct cam_industrial *data) {
    if (!d || !data) {
        errno = EINVAL;
        return -1;
    }
    return ioctl(d->fd, CAM_GET_INDUSTRIAL, data);
}
int rgb_device_industrial_config(struct jpeg_device *d, unsigned config) {
    if (!d) {
        errno = EINVAL;
        return -1;
    }
    return ioctl(d->fd, CAM_SET_INDUSTRIAL, &config);
}
int rgb_device_industrial_get_config(struct jpeg_device *d, unsigned *config) {
    if (!d || !config) {
        errno = EINVAL;
        return -1;
    }
    return ioctl(d->fd, CAM_GET_INDUSTRIAL_CONFIG, config);
}

int rgb_device_processing_status(struct jpeg_device *d, uint32_t values[4]) {
    if (!d || !values) {
        errno = EINVAL;
        return -1;
    }
    return ioctl(d->fd, CAM_GET_PROCESSING, values);
}

int rgb_device_set_isp(struct jpeg_device *d, const struct rgb_isp_config *config) {
    if (!d || !d->started || !config) {
        errno = EINVAL;
        return -1;
    }
    struct cam_isp isp = {
        config->rg,         config->bg,       config->contrast, (uint32_t)config->brightness,
        config->saturation, config->gaussian, config->he,       config->revision};
    return ioctl(d->fd, CAM_SET_ISP, &isp);
}

int rgb_device_copy_channel(struct jpeg_device *d, unsigned channel, uint32_t last, uint8_t *out,
                            size_t cap, uint32_t meta[24]) {
    if (!d || !d->started || channel > 2 || !meta)
        return -4;
    memset(meta, 0, JPEG_META_WORDS * sizeof(*meta));
    if (!channel)
        return jpeg_copy_ring(d->pool, 2u * RGB_RAW_PREVIEW_STRIDE, last, out, cap, meta, 1920,
                              1080, RGB_RAW_PREVIEW_STRIDE, RGB_RAW_PREVIEW_META);
    unsigned base = channel == 1 ? JPEG_ROI_BASE : JPEG_CAPTURE_BASE;
    int rc = jpeg_copy_ring(d->pool + base, JPEG_SLOT_STRIDE, last, out, cap, meta, 0, 0,
                            JPEG_SLOT_STRIDE, JPEG_META_OFFSET);
    if (rc > 0 && ((meta[9] >> 8) & 255) != channel)
        return -3;
    return rc;
}

int rgb_sensor_submit(struct jpeg_device *d, const struct cam_sensor_request *r) {
    return ioctl(d->fd, CAM_SENSOR_SUBMIT, r);
}
int rgb_sensor_status(struct jpeg_device *d, struct cam_sensor_status *s) {
    return ioctl(d->fd, CAM_SENSOR_STATUS, s);
}
int rgb_quality_origin(struct jpeg_device *d, unsigned v) {
    return ioctl(d->fd, CAM_QUALITY_ORIGIN, &v);
}
int rgb_quality_snapshot(struct jpeg_device *d, struct cam_quality *s) {
    return ioctl(d->fd, CAM_QUALITY_SNAPSHOT, s);
}

int rgb_device_white_balance(struct jpeg_device *d, unsigned manual, unsigned r, unsigned b) {
    if (!d || !d->started) {
        errno = EINVAL;
        return -1;
    }
    struct cam_wb wb = {manual, r, b};
    return ioctl(d->fd, CAM_SET_WB, &wb);
}

int rgb_enhance_submit(struct jpeg_device *d, const struct cam_enhance *r) {
    return ioctl(d->fd, CAM_ENHANCE_SUBMIT, r);
}
int rgb_enhance_status(struct jpeg_device *d, struct cam_enhance_status *s) {
    return ioctl(d->fd, CAM_ENHANCE_STATUS, s);
}

int rgb_device_stream_rect(struct jpeg_device *d, unsigned id, unsigned x, unsigned y, unsigned w,
                           unsigned h) {
    return rgb_device_stream_rect_channel(d, 1, id, x, y, w, h);
}
int rgb_device_stream_rect_channel(struct jpeg_device *d, unsigned channel, unsigned id, unsigned x,
                                   unsigned y, unsigned w, unsigned h) {
    if (!d || !d->started) {
        errno = EINVAL;
        return -1;
    }
    struct cam_rect command = {id, x, y, w, h, channel};
    return ioctl(d->fd, channel == 2 ? CAM_REQUEST_RECT : CAM_STREAM_RECT, &command);
}

int rgb_device_process_submit(struct jpeg_device *d, const uint32_t words[10], uint32_t *revision) {
    if (!d || !words || !revision) {
        errno = EINVAL;
        return -1;
    }
    struct cam_process_config p;
    memcpy(p.words, words, sizeof(p.words));
    p.revision = *revision;
    int rc = ioctl(d->fd, CAM_PROCESS_SUBMIT, &p);
    if (!rc)
        *revision = p.revision;
    return rc;
}
int rgb_device_process_status(struct jpeg_device *d, uint32_t values[32]) {
    if (!d || !values) {
        errno = EINVAL;
        return -1;
    }
    return ioctl(d->fd, CAM_PROCESS_STATUS, values);
}
