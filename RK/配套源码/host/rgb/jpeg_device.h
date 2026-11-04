#ifndef JPEG_DEVICE_H
#define JPEG_DEVICE_H
#include <stdint.h>
#include <stddef.h>
struct jpeg_device;
struct jpeg_device *jpeg_device_open(const char *path);
int jpeg_device_start(struct jpeg_device *device, unsigned gaussian, unsigned equalize);
int jpeg_device_copy(struct jpeg_device *device, uint32_t last, uint8_t *out, size_t cap,
                     uint32_t meta[24]);
int rgb_device_copy_channel(struct jpeg_device *, unsigned, uint32_t, uint8_t *, size_t,
                            uint32_t[24]);
int jpeg_device_status(struct jpeg_device *device, uint32_t status[4]);
int jpeg_device_close(struct jpeg_device *device);
struct rgb_isp_config {
    uint32_t rg, bg, contrast;
    int32_t brightness;
    uint32_t saturation, gaussian, he, revision;
};
int rgb_device_set_isp(struct jpeg_device *, const struct rgb_isp_config *);
struct cam_industrial;
int rgb_device_industrial(struct jpeg_device *, struct cam_industrial *);
int rgb_device_industrial_config(struct jpeg_device *, unsigned);
int rgb_device_industrial_get_config(struct jpeg_device *, unsigned *);
int rgb_device_processing_status(struct jpeg_device *, uint32_t[4]);

int rgb_device_stream_rect_channel(struct jpeg_device *, unsigned, unsigned, unsigned, unsigned,
                                   unsigned, unsigned);
int rgb_device_request_rect_channel(struct jpeg_device *, unsigned, unsigned, unsigned, unsigned,
                                    unsigned, unsigned);
int rgb_device_process_submit(struct jpeg_device *, const uint32_t[10], uint32_t *);
int rgb_device_process_status(struct jpeg_device *, uint32_t[32]);
#endif
