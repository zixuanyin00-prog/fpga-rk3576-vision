#ifndef CAMERA_JPEG_UAPI_H
#define CAMERA_JPEG_UAPI_H
#include <linux/types.h>
#include <linux/ioctl.h>
#define CAM_VERSION 0x001c0000u

#define CAM_CHUNK_SHIFT 20u
#define CAM_CHUNK_BYTES (1u << CAM_CHUNK_SHIFT)
#define CAM_CHUNK_COUNT 16u
#define CAM_LOGICAL_BASE 0x01000000u
#define CAM_SGM_INDEX 0x200u
#define CAM_SGM_BASE 0x204u
#define CAM_SGM_MAGIC 0x208u
#define CAM_SGM_CONFIG 0x20cu
#define CAM_SGM_VALID 0x210u
#define CAM_SGM_MAGIC_VALUE 0x314d4753u
#define CAM_SGM_CONFIG_VALUE 0x01001014u
#define CAM_SGM_ALL_VALID ((1u << CAM_CHUNK_COUNT) - 1u)
#define CAM_HOTUPDATE_VERSION 0x00140000u
#define CAM_PREVIEW60_VERSION 0x00130000u
#define CAM_SLOT_COUNT 5u
#define CAM_SLOT_STRIDE 0x200000u
#define CAM_META_OFFSET 0x1ff000u
#define CAM_POOL_BYTES 0x1000000u
#define CAM_PREVIEW_STRIDE 0x200000u
#define CAM_PREVIEW_META 0x1ff000u
#define CAM_PREVIEW_RAW 0x00010000u
#define CAM_RAW_PREVIEW_STRIDE 0x600000u
#define CAM_RAW_PREVIEW_META 0x5ff000u
#define CAM_RAW_PREVIEW_STOP 0xbffff0u
#define CAM_ROI_BASE 0xc00000u
#define CAM_CAPTURE_BASE 0xe00000u
#define CAM_MAX_ROI_PIXELS 518400u
#define CAM_ROI_BYTES (CAM_MAX_ROI_PIXELS * 3u)
#define CAM_STREAM_MAGIC 0x33525453u
#define CAM_STREAM_MAGIC_CSR 0x240u
#define CAM_EPOCH_CSR 0x244u
#define CAM_MAX_ROI_CSR 0x248u
#define CAM_STOP_OFFSET (CAM_POOL_BYTES - 16u)
#define CAM_FRAME_BYTES 6220800u
#define CAM_META_MAGIC 0x32434441u
#define CAM_STOP_MAGIC 0x53544f50u
#define CAM_FORMAT_JPEG 0x33424752u
struct cam_info {
    __u32 version, width, height, frame_bytes, slots, stride, meta_offset, pool_bytes;
};
struct cam_wb {
    __u32 manual, r_q8, b_q8;
};
struct cam_clock {
    __u64 before_ns, after_ns;
    __u32 tick, reserved;
};
struct cam_filters {
    __u32 gaussian, equalize;
};
struct cam_status {
    __u32 status, last_frame, bad_config, protocol;
};
#define CAM_GET_INFO _IOR('C', 0, struct cam_info)
#define CAM_START _IO('C', 1)
#define CAM_STOP _IO('C', 2)
#define CAM_SET_WB _IOW('C', 3, struct cam_wb)
#define CAM_GET_CLOCK _IOR('C', 4, struct cam_clock)
#define CAM_SET_FILTERS _IOW('C', 5, struct cam_filters)
struct cam_isp {
    __u32 rg, bg, contrast, brightness, saturation, gaussian, he, revision;
};
#define CAM_SET_ISP _IOW('C', 7, struct cam_isp)
#define CAM_GET_STATUS _IOR('C', 6, struct cam_status)
struct cam_roi {
    __u32 request_id, roi, x, y;
};
struct cam_rect {
    __u32 request_id, x, y, width, height, channel;
};
#define CAM_REQUEST_RECT _IOW('C', 21, struct cam_rect)
#define CAM_PREVIEW_COMPRESSION _IOW('C', 23, __u32)
#define CAM_STREAM_RECT _IOW('C', 22, struct cam_rect)
#define CAM_RECT_MAGIC 0x31544352u
#define CAM_REQUEST_ROI _IOW('C', 8, struct cam_roi)
struct cam_compression {
    __u32 lossless, adaptive, quality, target_kib, origin_x, origin_y;
};
#define CAM_SET_COMPRESSION _IOW('C', 8, struct cam_compression)
#define CAM_SET_MODE _IOW('C', 9, __u32)
struct cam_features {
    __u32 frame, fault;
    __u32 values[80];
};
#define CAM_GET_FEATURES _IOR('C', 10, struct cam_features)
#define CAM_SET_FEATURES _IOW('C', 11, __u32)
struct cam_industrial {
    __u32 values[32];
};
#define CAM_GET_INDUSTRIAL _IOR('C', 12, struct cam_industrial)
#define CAM_SET_INDUSTRIAL _IOW('C', 13, __u32)
#define CAM_GET_INDUSTRIAL_CONFIG _IOR('C', 14, __u32)

struct cam_processing {
    __u32 epoch, revision, industrial, features;
};
#define CAM_GET_PROCESSING _IOR('C', 26, struct cam_processing)

struct cam_sensor_request {
    __u32 request_id, shr0, gain_code, flags;
};
struct cam_sensor_status {
    __u32 magic, abi, busy, submitted_id;
    __u32 result[16];
};
struct cam_quality {
    __u32 values[24];
};
#define CAM_SENSOR_SUBMIT _IOW('C', 15, struct cam_sensor_request)
#define CAM_SENSOR_STATUS _IOR('C', 16, struct cam_sensor_status)
#define CAM_QUALITY_ORIGIN _IOW('C', 17, __u32)
#define CAM_QUALITY_SNAPSHOT _IOR('C', 18, struct cam_quality)
#define CAM_SENSOR_MAGIC 0x31584d49u
#define CAM_SENSOR_READ_ONLY 1u
#define CAM_SENSOR_RESTORE 2u

struct cam_enhance {
    __u32 gain_q8, strength_q8, revision, knots[9];
};
struct cam_enhance_status {
    __u32 magic, abi;
    struct cam_enhance active;
    __u32 effective_revision, source_frame;
};
#define CAM_ENHANCE_MAGIC 0x31545541u
#define CAM_ENHANCE_SUBMIT _IOW('C', 19, struct cam_enhance)
#define CAM_ENHANCE_STATUS _IOR('C', 20, struct cam_enhance_status)

#define CAM_PROCESS_MAGIC 0x32435250u
#define CAM_PROCESS_ABI 1u
#define CAM_PROCESS_WORDS 10u
#define CAM_PROCESS_SUPPORTED 0xfffu
struct cam_process_config {
    __u32 words[CAM_PROCESS_WORDS], revision;
};
struct cam_process_status {
    __u32 magic, abi, supported_mask, word_count, requested_revision, effective_revision,
        effective_source, status, rejected;
    __u32 requested_words[CAM_PROCESS_WORDS], effective_words[CAM_PROCESS_WORDS], reserved[3];
};
#define CAM_PROCESS_SUBMIT _IOWR('C', 27, struct cam_process_config)
#define CAM_PROCESS_STATUS _IOR('C', 28, struct cam_process_status)
#endif
